from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from arq import Retry

from worker.tasks import render


@pytest.fixture
def infrastructure_failure(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []

    async def mark_job(_version_id: str, **fields: Any) -> None:
        updates.append(fields)

    async def load_version(_version_id: str, _user_id: str) -> Any:
        raise OSError("temporary database outage")

    monkeypatch.setattr(render, "_mark_job", mark_job)
    monkeypatch.setattr(render, "_load_version", load_version)
    silent_logger = SimpleNamespace(
        bind=lambda **_kwargs: SimpleNamespace(
            exception=lambda *_args, **_kw: None,
            error=lambda *_args, **_kw: None,
        )
    )
    monkeypatch.setattr(render, "logger", silent_logger)
    monkeypatch.setattr(
        render,
        "get_settings",
        lambda: SimpleNamespace(
            render_max_tries=2,
            render_retry_delay_seconds=7,
        ),
    )
    return updates


@pytest.mark.asyncio
async def test_transient_infrastructure_failure_uses_arq_retry(
    infrastructure_failure: list[dict[str, Any]],
) -> None:
    with pytest.raises(Retry) as raised:
        await render.render_resume_pdf(
            {"job_try": 1}, version_id="v1", user_id="u1", template="ats_classic"
        )

    assert raised.value.defer_score == 7_000
    assert [update["status"] for update in infrastructure_failure] == ["rendering", "queued"]
    assert infrastructure_failure[0]["error_message"] is None
    assert infrastructure_failure[-1]["error_message"] == (
        "Temporary render infrastructure failure; retry scheduled."
    )


@pytest.mark.asyncio
async def test_exhausted_infrastructure_retries_persist_terminal_failure(
    infrastructure_failure: list[dict[str, Any]],
) -> None:
    with pytest.raises(OSError, match="temporary database outage"):
        await render.render_resume_pdf(
            {"job_try": 2}, version_id="v1", user_id="u1", template="ats_classic"
        )

    assert [update["status"] for update in infrastructure_failure] == ["rendering", "failed"]
    assert infrastructure_failure[-1]["finished_at"] is not None
    assert infrastructure_failure[-1]["error_message"] == (
        "Render infrastructure failed after the configured retries."
    )


@pytest.mark.asyncio
async def test_ats_enqueue_failure_does_not_fail_a_persisted_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[dict[str, Any]] = []

    async def mark_job(_version_id: str, **fields: Any) -> None:
        updates.append(fields)

    async def load_version(_version_id: str, _user_id: str) -> Any:
        return SimpleNamespace(version=1, template="ats_classic", resume_id="r1")

    async def compile_pdf(_source: str, *, job_name: str) -> Any:
        assert job_name == "resume-1"
        return SimpleNamespace(
            page_count=1,
            pdf_sha256="abc123",
            pdf_bytes=b"%PDF-1.7",
            duration_ms=42,
        )

    async def update_version(*_args: Any, **_kwargs: Any) -> None:
        return None

    class FakeLogger:
        def bind(self, **_kwargs: Any) -> FakeLogger:
            return self

        def exception(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def error(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def warning(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def info(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    monkeypatch.setattr(render, "_mark_job", mark_job)
    monkeypatch.setattr(render, "_load_version", load_version)
    monkeypatch.setattr(render, "build_context", lambda _version: {})
    monkeypatch.setattr(render, "get_settings", lambda: SimpleNamespace(
        render_max_pages=3,
        render_max_tries=2,
        render_retry_delay_seconds=7,
        render_queue_name="renders",
    ))
    monkeypatch.setattr(render, "render_template", lambda *_args, **_kwargs: "latex source")
    monkeypatch.setattr(render, "compile_latex", compile_pdf)
    monkeypatch.setattr(render.storage, "relative_pdf_path", lambda **_kwargs: "user/resume/v1.pdf")
    monkeypatch.setattr(render.storage, "write_pdf", lambda *_args, **_kwargs: _async_none())
    monkeypatch.setattr(
        render.db,
        "db",
        lambda: {"resume_versions": SimpleNamespace(update_one=update_version)},
    )

    async def enqueue_score(*_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError("Redis temporarily unavailable")

    monkeypatch.setattr(render, "_enqueue_score", enqueue_score)
    monkeypatch.setattr(render, "logger", FakeLogger())

    result = await render.render_resume_pdf(
        {},
        version_id="111111111111111111111111",
        user_id="u1",
        template="ats_classic",
    )

    assert result["ok"] is True
    assert result["pdf_path"] == "user/resume/v1.pdf"
    assert [update["status"] for update in updates] == ["rendering", "succeeded"]
    assert updates[-1]["error_message"] is None


async def _async_none() -> None:
    return None
