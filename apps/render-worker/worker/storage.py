"""Artifact storage for rendered PDFs.

A local filesystem implementation behind a narrow interface, because that is
what a single-host dev stack wants and because the swap to S3/GCS later is then
a change to two functions rather than a search for `open()` calls. The Compose
stack mounts `render_storage` as a named volume shared with the API (read) and
the automation worker (read-only), which is exactly the coupling that would
break in a multi-host deployment -- so treat this module as the seam where
object storage goes in.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import structlog

from worker.settings import get_settings

logger = structlog.get_logger(__name__)

# Path components are ObjectId hex strings, so anything else is either a bug or
# an attempt at traversal. Validated rather than trusted: `user_id` reaches this
# function from a queue payload, and a queue is not an authenticated boundary.
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class UnsafePathError(ValueError):
    """A path component failed validation."""


def _check(component: str, *, name: str) -> str:
    if not _SAFE_COMPONENT.match(component):
        raise UnsafePathError(f"{name} is not a safe path component: {component!r}")
    return component


def relative_pdf_path(*, user_id: str, resume_id: str, version: int, sha256: str) -> str:
    """Build the storage-relative path for a rendered PDF.

    The content hash in the filename makes writes idempotent: re-rendering
    unchanged input lands on the same path with the same bytes, so a duplicate
    render is harmless rather than a half-overwritten file.
    """
    _check(user_id, name="user_id")
    _check(resume_id, name="resume_id")
    _check(sha256, name="sha256")
    return f"{user_id}/{resume_id}/v{version:04d}-{sha256[:12]}.pdf"


def absolute_path(relative: str) -> Path:
    """Resolve a storage-relative path, refusing anything that escapes the root."""
    root = get_settings().render_storage_dir.resolve()
    candidate = (root / relative).resolve()
    # `is_relative_to` is the check that matters; a crafted `..` in a stored
    # path would otherwise let a later read touch arbitrary files.
    if not candidate.is_relative_to(root):
        raise UnsafePathError(f"path escapes storage root: {relative!r}")
    return candidate


async def write_pdf(relative: str, data: bytes) -> Path:
    """Persist PDF bytes. Returns the absolute path written."""
    target = absolute_path(relative)

    def _write() -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a reader never observes a partial PDF. A crash
        # mid-write leaves a .tmp file rather than a corrupt artifact that the
        # ATS scorer would then happily score as zero.
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(target)
        return target

    # Offloaded: a 300KB write is fast but not instant, and blocking the event
    # loop stalls every other in-flight compile on this worker.
    path = await asyncio.to_thread(_write)
    logger.info("storage.pdf_written", path=relative, bytes=len(data))
    return path


async def read_pdf(relative: str) -> bytes:
    """Read a stored PDF. Used by the ATS scorer."""
    return await asyncio.to_thread(absolute_path(relative).read_bytes)
