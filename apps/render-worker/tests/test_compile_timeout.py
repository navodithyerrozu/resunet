from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from worker import compile as compile_module
from worker.compile import LatexCompileError


class HangingProcess:
    def __init__(self) -> None:
        self.killed = False
        self.waited = False

    async def communicate(self) -> tuple[bytes, None]:
        await asyncio.Event().wait()
        return b"", None

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        return -9


@pytest.mark.asyncio
async def test_latex_timeout_kills_and_reaps_process(monkeypatch: pytest.MonkeyPatch) -> None:
    process = HangingProcess()

    async def start_process(*_args: Any, **_kwargs: Any) -> HangingProcess:
        return process

    monkeypatch.setattr(compile_module.asyncio, "create_subprocess_exec", start_process)

    with pytest.raises(LatexCompileError, match="was killed"):
        await compile_module._run_pdflatex(
            Path("resume.tex"), Path("."), limit_seconds=0.001
        )

    assert process.killed
    assert process.waited
