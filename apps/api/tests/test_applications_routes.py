"""Application tracker persistence and automation safety tests."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db.mongo import Collections
from app.main import create_app
from app.models.user import User, UserRole, UserStatus

USER_ID = ObjectId("111111111111111111111111")
JOB_ID = ObjectId("222222222222222222222222")
VERSION_ID = ObjectId("333333333333333333333333")


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def find_one(self, query: dict[str, Any], *_: object, **__: object) -> Any:
        for doc in self.docs:
            if all(
                (not isinstance(value, dict) and doc.get(key) == value)
                or (
                    isinstance(value, dict)
                    and "$nin" in value
                    and doc.get(key) not in value["$nin"]
                )
                for key, value in query.items()
            ):
                return deepcopy(doc)
        return None

    async def insert_one(self, doc: dict[str, Any], **__: object) -> Any:
        stored = deepcopy(doc)
        stored.setdefault("_id", ObjectId())
        self.docs.append(stored)
        return type("InsertResult", (), {"inserted_id": stored["_id"]})()

    async def find_one_and_update(
        self, query: dict[str, Any], update: dict[str, Any], **__: object
    ) -> Any:
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                doc.update(deepcopy(update.get("$set", {})))
                for key, value in update.get("$push", {}).items():
                    doc.setdefault(key, []).append(deepcopy(value))
                return deepcopy(doc)
        return None


class FakeDB:
    def __init__(self) -> None:
        self.collections = {item: FakeCollection() for item in Collections}

    def __getitem__(self, name: Collections) -> FakeCollection:
        return self.collections[name]


@pytest.fixture
def applications_app(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, FakeDB]:
    db = FakeDB()

    async def noop() -> None:
        return None

    monkeypatch.setattr("app.main.connect", noop)
    monkeypatch.setattr("app.main.disconnect", noop)
    monkeypatch.setattr("app.main.connect_queues", noop)
    monkeypatch.setattr("app.main.disconnect_queues", noop)
    monkeypatch.setattr("app.api.v1.routes.applications.collection", lambda name: db[name])
    monkeypatch.setattr("app.api.deps.collection", lambda name: db[name])
    db[Collections.JOBS].docs.append(
        {
            "_id": JOB_ID,
            "status": "open",
            "supports_automation": False,
            "source": {"url": "https://jobs.example.com/role"},
        }
    )
    db[Collections.RESUME_VERSIONS].docs.append(
        {"_id": VERSION_ID, "user_id": USER_ID, "pdf_path": "rendered.pdf"}
    )

    application = create_app()
    fake_hash = "not-used"

    async def current_user() -> User:
        return User(
            id=str(USER_ID),
            email="person@example.com",
            password_hash=fake_hash,
            full_name="Example Person",
            status=UserStatus.ACTIVE,
            roles=[UserRole.USER],
            token_version=1,
        )

    application.dependency_overrides[get_current_user] = current_user
    return application, db


def test_manual_application_persists_as_draft_without_queue(
    applications_app: tuple[Any, FakeDB], monkeypatch: pytest.MonkeyPatch
) -> None:
    application, db = applications_app

    async def unexpected_queue(**_: Any) -> None:
        raise AssertionError("manual tracking must not enqueue automation")

    monkeypatch.setattr("app.api.v1.routes.applications.enqueue_submission", unexpected_queue)
    payload = {"jobId": str(JOB_ID), "resumeVersionId": str(VERSION_ID), "method": "manual"}
    with TestClient(application) as client:
        response = client.post("/api/v1/applications", json=payload)
        duplicate = client.post("/api/v1/applications", json=payload)

    assert response.status_code == 201
    assert response.json()["status"] == "draft"
    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == response.json()["id"]
    assert len(db[Collections.APPLICATIONS].docs) == 1
    assert db[Collections.APPLICATIONS].docs[0]["job_id"] == JOB_ID


def test_manual_job_cannot_be_queued_for_automation(
    applications_app: tuple[Any, FakeDB],
) -> None:
    application, _ = applications_app
    payload = {"jobId": str(JOB_ID), "resumeVersionId": str(VERSION_ID), "method": "automated"}
    with TestClient(application) as client:
        response = client.post("/api/v1/applications", json=payload)
    assert response.status_code == 409
    assert "automation adapter" in response.json()["error"]["message"]


def test_user_can_advance_draft_and_event_is_recorded(
    applications_app: tuple[Any, FakeDB],
) -> None:
    application, _ = applications_app
    payload = {"jobId": str(JOB_ID), "resumeVersionId": str(VERSION_ID), "method": "manual"}
    with TestClient(application) as client:
        created = client.post("/api/v1/applications", json=payload).json()
        updated = client.patch(
            f"/api/v1/applications/{created['id']}/status", json={"status": "submitted"}
        )

    assert updated.status_code == 200
    assert updated.json()["status"] == "submitted"
    assert updated.json()["submitted_at"] is not None
    assert updated.json()["events"][-1]["kind"] == "status_change"


def _seed_worker_application(db: FakeDB, status: str, outcome: str) -> ObjectId:
    application_id = ObjectId("444444444444444444444444")
    db[Collections.APPLICATIONS].docs.append(
        {
            "_id": application_id,
            "user_id": USER_ID,
            "job_id": JOB_ID,
            "resume_version_id": VERSION_ID,
            "status": status,
            "method": "automated",
            "attempts": [
                {
                    "attempt_number": 1,
                    "started_at": datetime.now(UTC),
                    "outcome": outcome,
                    "failed_step": (
                        "await_approval"
                        if status == "awaiting_approval"
                        else "open_application"
                    ),
                    "field_mappings": [
                        {
                            "selector": "#name",
                            "label": "Name",
                            "value_preview": "Alex Example",
                            "strategy": "label_match",
                            "confidence": 0.99,
                        }
                    ],
                }
            ],
            "events": [],
        }
    )
    return application_id


def test_approval_persists_queued_state_before_enqueuing(
    applications_app: tuple[Any, FakeDB], monkeypatch: pytest.MonkeyPatch
) -> None:
    application, db = applications_app
    application_id = _seed_worker_application(db, "awaiting_approval", "needs_human")
    observed: list[str] = []

    async def enqueue(**_: Any) -> str:
        current = await db[Collections.APPLICATIONS].find_one({"_id": application_id})
        observed.append(current["status"])
        return "arq-approved-job"

    monkeypatch.setattr("app.api.v1.routes.applications.enqueue_submission", enqueue)
    with TestClient(application) as client:
        response = client.post(f"/api/v1/applications/{application_id}/approve")

    assert response.status_code == 200
    assert observed == ["queued"]
    assert response.json()["status"] == "queued"
    assert response.json()["arq_job_id"] == "arq-approved-job"


def test_approval_queue_failure_restores_approval_state(
    applications_app: tuple[Any, FakeDB], monkeypatch: pytest.MonkeyPatch
) -> None:
    application, db = applications_app
    application_id = _seed_worker_application(db, "awaiting_approval", "needs_human")

    async def failed_queue(**_: Any) -> str:
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("app.api.v1.routes.applications.enqueue_submission", failed_queue)
    with TestClient(application) as client:
        response = client.post(f"/api/v1/applications/{application_id}/approve")

    stored = db[Collections.APPLICATIONS].docs[0]
    assert response.status_code == 503
    assert stored["status"] == "awaiting_approval"
    assert stored["events"][-1]["kind"] == "queue_failure"


def test_retry_persists_queued_state_before_enqueuing(
    applications_app: tuple[Any, FakeDB], monkeypatch: pytest.MonkeyPatch
) -> None:
    application, db = applications_app
    application_id = _seed_worker_application(db, "failed", "retryable_failure")
    observed: list[str] = []

    async def enqueue(**_: Any) -> str:
        current = await db[Collections.APPLICATIONS].find_one({"_id": application_id})
        observed.append(current["status"])
        return "arq-retry-job"

    monkeypatch.setattr("app.api.v1.routes.applications.enqueue_submission", enqueue)
    with TestClient(application) as client:
        response = client.post(f"/api/v1/applications/{application_id}/retry")

    assert response.status_code == 202
    assert observed == ["queued"]
    assert response.json()["status"] == "queued"
    assert response.json()["arq_job_id"] == "arq-retry-job"


def test_retry_queue_failure_restores_failed_state(
    applications_app: tuple[Any, FakeDB], monkeypatch: pytest.MonkeyPatch
) -> None:
    application, db = applications_app
    application_id = _seed_worker_application(db, "failed", "retryable_failure")

    async def failed_queue(**_: Any) -> str:
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("app.api.v1.routes.applications.enqueue_submission", failed_queue)
    with TestClient(application) as client:
        response = client.post(f"/api/v1/applications/{application_id}/retry")

    stored = db[Collections.APPLICATIONS].docs[0]
    assert response.status_code == 503
    assert stored["status"] == "failed"
    assert stored["events"][-1]["kind"] == "queue_failure"
