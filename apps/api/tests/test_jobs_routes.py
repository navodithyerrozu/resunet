"""API tests for user-submitted jobs, deduplication, and user-specific matches."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db.mongo import Collections
from app.main import create_app
from app.models.profile import CareerProfile, ContactBlock, Skill
from app.models.user import User, UserRole, UserStatus

USER_ID = ObjectId("111111111111111111111111")


def _get(doc: dict[str, Any], key: str) -> Any:
    value: Any = doc
    for segment in key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(segment)
    return value


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    return all(
        str(_get(doc, key)) == str(value)
        for key, value in query.items()
        if not isinstance(value, dict)
    )


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def find_one(
        self, query: dict[str, Any], *_: object, **__: object
    ) -> dict[str, Any] | None:
        row = next((doc for doc in self.docs if _matches(doc, query)), None)
        return deepcopy(row) if row is not None else None

    async def insert_one(self, doc: dict[str, Any], **__: object) -> Any:
        stored = deepcopy(doc)
        stored.setdefault("_id", ObjectId())
        self.docs.append(stored)
        return type("InsertResult", (), {"inserted_id": stored["_id"]})()

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
        **__: object,
    ) -> Any:
        row = next((doc for doc in self.docs if _matches(doc, query)), None)
        if row is None and upsert:
            row = dict(query)
            row["_id"] = ObjectId()
            self.docs.append(row)
        if row is not None:
            row.update(deepcopy(update.get("$set", {})))
        return type("UpdateResult", (), {"matched_count": int(row is not None)})()


class FakeDB:
    def __init__(self) -> None:
        self.collections = {item: FakeCollection() for item in Collections}

    def __getitem__(self, name: Collections) -> FakeCollection:
        return self.collections[name]


def _user() -> User:
    fake_hash = "not-used"
    return User(
        id=str(USER_ID),
        email="person@example.com",
        password_hash=fake_hash,
        full_name="Example Person",
        status=UserStatus.ACTIVE,
        roles=[UserRole.USER],
        token_version=1,
    )


@pytest.fixture
def jobs_app(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, FakeDB]:
    db = FakeDB()

    async def noop() -> None:
        return None

    monkeypatch.setattr("app.main.connect", noop)
    monkeypatch.setattr("app.main.disconnect", noop)
    monkeypatch.setattr("app.main.connect_queues", noop)
    monkeypatch.setattr("app.main.disconnect_queues", noop)
    monkeypatch.setattr("app.api.v1.routes.jobs.collection", lambda name: db[name])

    profile = CareerProfile(
        user_id=str(USER_ID),
        contact=ContactBlock(full_name="Example Person", email="person@example.com"),
        skills=[Skill(name="Python")],
    )
    profile_doc = profile.to_mongo()
    profile_doc["user_id"] = USER_ID
    db[Collections.CAREER_PROFILES].docs.append(profile_doc)

    application = create_app()

    async def current_user() -> User:
        return _user()

    application.dependency_overrides[get_current_user] = current_user
    return application, db


def test_ingest_persists_manual_job_and_deduplicates(jobs_app: tuple[Any, FakeDB]) -> None:
    application, db = jobs_app
    payload = {
        "title": "Senior Backend Engineer",
        "companyName": "Example Inc",
        "descriptionRaw": "Required Python and Kubernetes. Minimum 5 years experience.",
        "status": "open",
    }
    with TestClient(application) as client:
        first = client.post("/api/v1/jobs", json=payload)
        second = client.post("/api/v1/jobs", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert len(db[Collections.JOBS].docs) == 1
    assert len(db[Collections.JOB_MATCHES].docs) == 1
    stored_match = db[Collections.JOB_MATCHES].docs[0]
    assert stored_match["matched_keywords"] == ["Python"]
    assert stored_match["missing_keywords"] == ["Kubernetes"]
    assert db[Collections.JOBS].docs[0]["supports_automation"] is False


def test_get_job_requires_a_user_match_or_application(jobs_app: tuple[Any, FakeDB]) -> None:
    application, _ = jobs_app
    with TestClient(application) as client:
        response = client.get(f"/api/v1/jobs/{ObjectId()}")
    assert response.status_code == 404


def test_ingest_rejects_url_only_without_scraping(jobs_app: tuple[Any, FakeDB]) -> None:
    application, _ = jobs_app
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/jobs",
            json={
                "url": "https://jobs.example.com/role/1",
                "title": "Engineer",
                "companyName": "Example",
            },
        )
    assert response.status_code == 422
