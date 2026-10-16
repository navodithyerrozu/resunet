from __future__ import annotations

import asyncio
from typing import Any

import pytest
from bson import ObjectId

from app.api.v1.routes import ats
from app.core.errors import ConflictError, QueueUnavailableError
from app.models.ats import ScoreRequest
from app.models.user import User, UserRole, UserStatus

USER_ID = ObjectId("111111111111111111111111")
VERSION_ID = ObjectId("222222222222222222222222")


def _user() -> User:
    return User(
        id=str(USER_ID),
        email="ats@example.com",
        password_hash="synthetic-test-hash",  # noqa: S106 - synthetic test fixture
        full_name="ATS Tester",
        status=UserStatus.ACTIVE,
        roles=[UserRole.USER],
        token_version=1,
    )


def test_score_queues_only_a_completed_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, str | None]] = []

    async def fetch_owned(*_: Any, **__: Any) -> dict[str, str]:
        return {"pdf_path": "renders/test.pdf"}

    async def enqueue(**kwargs: str | None) -> str:
        calls.append(kwargs)
        return "score:test-job"

    monkeypatch.setattr(ats, "fetch_owned", fetch_owned)
    monkeypatch.setattr(ats, "enqueue_score", enqueue)
    result = asyncio.run(ats.score_resume(ScoreRequest(resume_version_id=str(VERSION_ID)), _user()))

    assert result.status == "queued"
    assert result.arq_job_id == "score:test-job"
    assert calls == [{"version_id": str(VERSION_ID), "user_id": str(USER_ID), "job_id": None}]


def test_score_rejects_unrendered_version(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fetch_owned(*_: Any, **__: Any) -> dict[str, Any]:
        return {"pdf_path": None}

    async def should_not_enqueue(**_: Any) -> None:
        raise AssertionError("an unrendered version must not be queued")

    monkeypatch.setattr(ats, "fetch_owned", fetch_owned)
    monkeypatch.setattr(ats, "enqueue_score", should_not_enqueue)
    with pytest.raises(ConflictError):
        asyncio.run(ats.score_resume(ScoreRequest(resume_version_id=str(VERSION_ID)), _user()))


def test_score_does_not_claim_queued_when_queue_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fetch_owned(*_: Any, **__: Any) -> dict[str, str]:
        return {"pdf_path": "renders/test.pdf"}

    async def fail_enqueue(**_: Any) -> None:
        raise ConnectionError("redis unavailable")

    monkeypatch.setattr(ats, "fetch_owned", fetch_owned)
    monkeypatch.setattr(ats, "enqueue_score", fail_enqueue)
    with pytest.raises(QueueUnavailableError):
        asyncio.run(ats.score_resume(ScoreRequest(resume_version_id=str(VERSION_ID)), _user()))
