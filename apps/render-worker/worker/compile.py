"""Invoking pdflatex safely.

Escaping (`latex.py`) is the first line of defence. This is the second, on the
assumption that the first one has a hole in it: even given a `\\write18` that
slipped through, the flags below mean it cannot execute, cannot read outside
its scratch directory, and cannot run longer than the configured timeout.

Defence in depth matters here more than usual because the attack surface is a
text field on a resume, the payload is invisible in normal use, and the output
is a PDF the attacker gets to download.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

import structlog

from worker.settings import get_settings

logger = structlog.get_logger(__name__)

# Deterministic builds: pdflatex stamps CreationDate into the PDF trailer, so
# without a pinned epoch two identical compiles differ and pdf_sha256 becomes
# useless for deduplication. 2020-01-01T00:00:00Z, chosen only for being fixed.
_SOURCE_DATE_EPOCH = "1577836800"

# Lines worth surfacing from a 3000-line pdflatex log. Everything else is noise
# about font maps.
_ERROR_LINE = re.compile(r"^(?:!|.*?:\d+:|LaTeX Error:|Runaway argument)", re.MULTILINE)
_PAGE_COUNT = re.compile(r"Output written on .*?\((\d+) pages?", re.DOTALL)


class LatexCompileError(RuntimeError):
    """Compilation failed. Carries the log tail for `RenderJob.latex_log_tail`."""

    def __init__(self, message: str, *, log_tail: str, returncode: int | None = None) -> None:
        super().__init__(message)
        self.log_tail = log_tail
        self.returncode = returncode


@dataclass(frozen=True, slots=True)
class CompileResult:
    pdf_bytes: bytes
    pdf_sha256: str
    page_count: int | None
    duration_ms: int
    log_tail: str


def _sandbox_env() -> dict[str, str]:
    """Environment for the pdflatex subprocess.

    Built from scratch rather than inherited. The worker's own environment
    holds the Mongo URI and Redis credentials, and there is no reason a TeX
    process should be able to read them -- `\\input{|env}` style tricks aside,
    least privilege costs nothing here.
    """
    settings = get_settings()
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": "/tmp",  # noqa: S108 - deliberate: kpathsea needs a writable HOME
        "TEXMFVAR": "/tmp/texmf-var",  # noqa: S108
        # openout_any=p forbids writing outside the current directory tree;
        # openin_any=p forbids reading outside it. Together these neutralise
        # \input{/etc/passwd} and \write to arbitrary paths even if a payload
        # survives escaping.
        "openout_any": "p",
        "openin_any": "p",
        "shell_escape": "f",
        "max_print_line": "1000",  # stop TeX wrapping errors mid-word
        "SOURCE_DATE_EPOCH": _SOURCE_DATE_EPOCH,
        "FORCE_SOURCE_DATE": "1",
        "TZ": "UTC",
        "LANG": "C.UTF-8",
        "_": str(settings.latex_passes),  # keeps the value observable in ps output
    }


def _extract_log_tail(log: str, *, limit: int = 2048) -> str:
    """Pull the useful part of a pdflatex log.

    Prefers actual error lines; falls back to the tail. pdflatex's own summary
    is at the end, but the *cause* is usually a few hundred lines earlier, so
    grepping for error markers beats blindly truncating.
    """
    errors = _ERROR_LINE.findall(log)
    if errors:
        matches = [m.group(0) for m in _ERROR_LINE.finditer(log)]
        joined = "\n".join(matches)
        if joined:
            return joined[-limit:]
    return log[-limit:]


async def compile_latex(
    source: str,
    *,
    job_name: str = "resume",
) -> CompileResult:
    """Compile LaTeX source to PDF bytes.

    Runs in a fresh temporary directory that is removed afterwards, so a
    compile cannot see artifacts from any other user's render -- relevant
    because `\\input` resolves relative to the working directory.
    """
    settings = get_settings()
    loop = asyncio.get_running_loop()
    started = loop.time()

    # mkdtemp rather than TemporaryDirectory: cleanup is explicit in `finally`
    # so a timeout kill path cannot leave the directory mounted while pdflatex
    # still holds file handles in it.
    workdir = Path(tempfile.mkdtemp(prefix="resunet-render-"))
    try:
        tex_path = workdir / f"{job_name}.tex"
        tex_path.write_text(source, encoding="utf-8")

        log = ""
        for pass_number in range(1, settings.latex_passes + 1):
            returncode, output = await _run_pdflatex(
                tex_path, workdir, timeout=settings.latex_compile_timeout_seconds
            )
            log = output
            if returncode != 0:
                raise LatexCompileError(
                    f"pdflatex exited {returncode} on pass {pass_number}",
                    log_tail=_extract_log_tail(output),
                    returncode=returncode,
                )

        pdf_path = workdir / f"{job_name}.pdf"
        if not pdf_path.exists():
            # pdflatex can exit 0 while producing nothing, typically when the
            # document body is empty. Checking the artifact rather than trusting
            # the exit code catches it.
            raise LatexCompileError(
                "pdflatex reported success but produced no PDF",
                log_tail=_extract_log_tail(log),
            )

        pdf_bytes = pdf_path.read_bytes()
        page_match = _PAGE_COUNT.search(log)
        duration_ms = int((loop.time() - started) * 1000)

        result = CompileResult(
            pdf_bytes=pdf_bytes,
            pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            page_count=int(page_match.group(1)) if page_match else None,
            duration_ms=duration_ms,
            log_tail=_extract_log_tail(log),
        )
        logger.info(
            "latex.compiled",
            bytes=len(pdf_bytes),
            pages=result.page_count,
            duration_ms=duration_ms,
        )
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _run_pdflatex(
    tex_path: Path, workdir: Path, *, timeout: int
) -> tuple[int | None, str]:
    """One pdflatex pass. Returns (returncode, combined output)."""
    process = await asyncio.create_subprocess_exec(
        "pdflatex",
        # Never stop for interactive input. Without this a syntax error makes
        # TeX prompt on stdin and the subprocess hangs until the timeout --
        # a failure that looks like slowness rather than an error.
        "-interaction=nonstopmode",
        "-halt-on-error",
        "-no-shell-escape",
        "-file-line-error",
        f"-output-directory={workdir}",
        tex_path.name,
        cwd=workdir,
        env=_sandbox_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
    )

    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        # SIGKILL, not SIGTERM: a TeX process stuck in a macro expansion loop
        # does not service signals politely, and a lingering pdflatex holds a
        # core for as long as it survives.
        process.kill()
        await process.wait()
        logger.error("latex.timeout", timeout_seconds=timeout)
        raise LatexCompileError(
            f"pdflatex exceeded {timeout}s and was killed",
            log_tail="Compile timed out. Common causes: a recursive macro, or an "
            "unclosed environment causing TeX to scan to end of file.",
        ) from None

    return process.returncode, stdout.decode("utf-8", errors="replace")
