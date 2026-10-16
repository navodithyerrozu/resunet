from __future__ import annotations

import asyncio
from typing import Any

import pytest
from arq import Retry
from bson import ObjectId

from worker.ats import ExtractionResult
from worker.tasks import score as score_task

USER_ID = ObjectId("111111111111111111111111")
VERSION_ID = ObjectId("222222222222222222222222")
RESUME_ID = ObjectId("333333333333333333333333")


class _Collection:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result
        self.inserted: list[dict[str, Any]] = []
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.upserts: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.projections: list[dict[str, Any] | None] = []

    async def find_one(
        self,
        _query: dict[str, Any],
        projection: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        self.projections.append(projection)
        return self.result

    async def insert_one(self, doc: dict[str, Any]) -> None:
        self.inserted.append(doc)

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> None:
        self.updates.append((query, update))
        if upsert:
            self.upserts.append((query, update))


class _Database:
    def __init__(self, collections: dict[str, _Collection]) -> None:
        self.collections = collections

    def __getitem__(self, name: str) -> _Collection:
        return self.collections[name]


def test_score_task_persists_extraction_and_updates_resume_score(monkeypatch: Any) -> None:
    version_collection = _Collection(
        {"pdf_path": "renders/resume.pdf", "page_count": 1, "resume_id": RESUME_ID}
    )
    score_collection = _Collection()
    resume_collection = _Collection()
    db = _Database(
        {
            "resume_versions": version_collection,
            "ats_scores": score_collection,
            "resumes": resume_collection,
        }
    )
    monkeypatch.setattr(score_task.db, "db", lambda: db)

    async def read_pdf(_path: str) -> bytes:
        return b"pdf bytes"

    monkeypatch.setattr(score_task.storage, "read_pdf", read_pdf)
    extracted = "Ada Lovelace ada@example.com " + (
        "Experience 2020-2024 delivered 42% impact. " * 8
    )
    monkeypatch.setattr(
        score_task.ats,
        "extract_text",
        lambda _data: ExtractionResult(extracted, 1, 1.0),
    )

    result = asyncio.run(
        score_task.score_resume_ats(
            {}, version_id=str(VERSION_ID), user_id=str(USER_ID), job_id=None
        )
    )

    assert result["ok"] is True
    assert version_collection.projections[0] == {
        "pdf_path": 1,
        "page_count": 1,
        "resume_id": 1,
    }
    score_key, score_update = score_collection.upserts[0]
    assert score_key == {
        "idempotency_key": f"{VERSION_ID}:generic:{score_task.ENGINE_VERSION}"
    }
    stored_score = score_update["$setOnInsert"]
    assert stored_score["user_id"] == USER_ID
    assert stored_score["resume_version_id"] == VERSION_ID
    assert stored_score["extracted_text"] == extracted
    assert stored_score["idempotency_key"] == score_key["idempotency_key"]
    assert resume_collection.updates[0][0] == {"_id": RESUME_ID, "user_id": USER_ID}
    assert resume_collection.updates[0][1]["$set"]["latest_ats_score"] == result["overall"]


def _settings() -> Any:
    return type("Settings", (), {"render_max_tries": 2, "render_retry_delay_seconds": 9})()


def test_score_task_retries_transient_failures_once(monkeypatch: Any) -> None:
    version_collection = _Collection(
        {"pdf_path": "renders/resume.pdf", "page_count": 1, "resume_id": RESUME_ID}
    )
    monkeypatch.setattr(
        score_task.db,
        "db",
        lambda: _Database({"resume_versions": version_collection}),
    )

    async def unavailable(_path: str) -> bytes:
        raise ConnectionError("storage unavailable")

    monkeypatch.setattr(score_task.storage, "read_pdf", unavailable)
    monkeypatch.setattr(score_task, "get_settings", _settings)

    with pytest.raises(Retry) as raised:
        asyncio.run(
            score_task.score_resume_ats(
                {"job_try": 1}, version_id=str(VERSION_ID), user_id=str(USER_ID)
            )
        )

    assert raised.value.defer_score == 9_000


def test_score_task_stops_retrying_after_attempt_limit(monkeypatch: Any) -> None:
    version_collection = _Collection(
        {"pdf_path": "renders/resume.pdf", "page_count": 1, "resume_id": RESUME_ID}
    )
    monkeypatch.setattr(
        score_task.db,
        "db",
        lambda: _Database({"resume_versions": version_collection}),
    )

    async def unavailable(_path: str) -> bytes:
        raise ConnectionError("storage unavailable")

    monkeypatch.setattr(score_task.storage, "read_pdf", unavailable)
    monkeypatch.setattr(score_task, "get_settings", _settings)

    with pytest.raises(ConnectionError, match="storage unavailable"):
        asyncio.run(
            score_task.score_resume_ats(
                {"job_try": 2}, version_id=str(VERSION_ID), user_id=str(USER_ID)
            )
        )


def test_job_keywords_are_loaded_from_the_parsed_contract(monkeypatch: Any) -> None:
    jobs = _Collection(
        {
            "parsed": {
                "keywords": [
                    {"term": "Python", "required": True},
                    {"term": "Kubernetes", "required": False},
                ]
            }
        }
    )
    monkeypatch.setattr(score_task.db, "db", lambda: _Database({"jobs": jobs}))

    result = asyncio.run(score_task._load_keywords(str(VERSION_ID)))

    assert result == {"required": ["Python"], "preferred": ["Kubernetes"]}
    assert jobs.projections == [{"parsed.keywords": 1}]
