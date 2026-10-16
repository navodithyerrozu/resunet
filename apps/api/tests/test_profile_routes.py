"""HTTP tests for career profile ownership, persistence, and experience ops."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo import ReturnDocument

from app.api.deps import get_current_user
from app.db.mongo import Collections
from app.main import create_app
from app.models.bullet import Bullet, BulletStatus
from app.models.profile import CareerProfile, ContactBlock, ExperienceEntry, ProjectEntry
from app.models.user import User, UserRole, UserStatus

ALICE_ID = ObjectId("111111111111111111111111")
BOB_ID = ObjectId("222222222222222222222222")
ENTRY_ID = "333333333333333333333333"
PROJECT_ID = "444444444444444444444444"


class _UpdateResult:
    def __init__(self, matched: int, modified: int) -> None:
        self.matched_count = matched
        self.modified_count = modified


def _same(left: object, right: object) -> bool:
    return str(left) == str(right)


def _matches(doc: dict[str, Any], filt: dict[str, Any]) -> bool:
    for key, expected in filt.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if not any(_same(actual, candidate) for candidate in expected["$in"]):
                return False
        elif not _same(actual, expected) and actual != expected:
            return False
    return True


class FakeCollection:
    """Tiny async collection: enough for the profile routes, no extra deps."""

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def insert_one(self, doc: dict[str, Any]) -> None:
        self.docs.append(deepcopy(doc))

    async def find_one(self, filt: dict[str, Any], **_: object) -> dict[str, Any] | None:
        for doc in self.docs:
            if _matches(doc, filt):
                return deepcopy(doc)
        return None

    async def find_one_and_replace(
        self,
        filt: dict[str, Any],
        replacement: dict[str, Any],
        return_document: object = ReturnDocument.AFTER,
        **_: object,
    ) -> dict[str, Any] | None:
        for index, doc in enumerate(self.docs):
            if _matches(doc, filt):
                self.docs[index] = deepcopy(replacement)
                chosen = replacement if return_document == ReturnDocument.AFTER else doc
                return deepcopy(chosen)
        return None

    async def find_one_and_update(
        self,
        filt: dict[str, Any],
        update: dict[str, Any],
        return_document: object = ReturnDocument.AFTER,
    ) -> dict[str, Any] | None:
        for doc in self.docs:
            if _matches(doc, filt):
                before = deepcopy(doc)
                self._apply(doc, update)
                chosen = doc if return_document == ReturnDocument.AFTER else before
                return deepcopy(chosen)
        return None

    async def update_one(
        self, filt: dict[str, Any], update: dict[str, Any], **_: object
    ) -> _UpdateResult:
        for doc in self.docs:
            if _matches(doc, filt):
                self._apply(doc, update)
                return _UpdateResult(1, 1)
        return _UpdateResult(0, 0)

    async def update_many(
        self, filt: dict[str, Any], update: dict[str, Any], **_: object
    ) -> _UpdateResult:
        modified = 0
        matched = 0
        for doc in self.docs:
            if _matches(doc, filt):
                matched += 1
                self._apply(doc, update)
                modified += 1
        return _UpdateResult(matched, modified)

    def _apply(self, doc: dict[str, Any], update: dict[str, Any]) -> None:
        if "$set" in update:
            doc.update(deepcopy(update["$set"]))
        if "$push" in update:
            for key, value in update["$push"].items():
                doc.setdefault(key, []).append(deepcopy(value))
        if "$pull" in update:
            for key, spec in update["$pull"].items():
                rows = doc.get(key, [])
                doc[key] = [row for row in rows if not _matches(row, spec)]


class FakeDB:
    def __init__(self) -> None:
        self._collections = {
            Collections.CAREER_PROFILES: FakeCollection(),
            Collections.BULLETS: FakeCollection(),
        }

    def __getitem__(self, name: Collections) -> FakeCollection:
        return self._collections[name]


class _Transaction:
    async def __aenter__(self) -> _Transaction:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


class _Session(_Transaction):
    def start_transaction(self) -> _Transaction:
        return _Transaction()


class _Client:
    async def start_session(self) -> _Session:
        return _Session()


def _user(user_id: ObjectId, email: str, name: str) -> User:
    return User(
        id=str(user_id),
        email=email,
        password_hash="argon2-not-a-real-hash",  # noqa: S106 - synthetic test hash.
        full_name=name,
        status=UserStatus.ACTIVE,
        roles=[UserRole.USER],
        token_version=1,
    )


ALICE = _user(ALICE_ID, "alice@example.com", "Alice Example")


def _contact(*, location: str | None = None) -> dict[str, str]:
    body = {"full_name": "Alice Example", "email": "alice@example.com"}
    if location is not None:
        body["location"] = location
    return body


async def _noop() -> None:
    return None


@pytest.fixture
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, db: FakeDB) -> Any:
    monkeypatch.setattr("app.main.connect", _noop)
    monkeypatch.setattr("app.main.disconnect", _noop)
    monkeypatch.setattr("app.main.connect_queues", _noop)
    monkeypatch.setattr("app.main.disconnect_queues", _noop)

    def fake_collection(name: Collections) -> FakeCollection:
        return db[name]

    monkeypatch.setattr("app.api.v1.routes.profile.collection", fake_collection)
    monkeypatch.setattr("app.api.v1.routes.profile.get_client", lambda: _Client())
    application = create_app()

    async def override_alice() -> User:
        return ALICE

    application.dependency_overrides[get_current_user] = override_alice
    return application


def _insert_profile(
    db: FakeDB,
    *,
    user_id: ObjectId,
    name: str,
    email: str,
    experiences: list[dict[str, Any]] | None = None,
    projects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    profile = CareerProfile(
        user_id=str(user_id),
        contact=ContactBlock(full_name=name, email=email),
        experiences=[ExperienceEntry.model_validate(row) for row in (experiences or [])],
        projects=[ProjectEntry.model_validate(row) for row in (projects or [])],
    )
    profile.completeness = profile.compute_completeness()
    doc = profile.model_dump(by_alias=True, exclude_none=True)
    doc["user_id"] = user_id
    doc["_id"] = ObjectId()
    db[Collections.CAREER_PROFILES].docs.append(doc)
    return doc


class TestGetProfile:
    def test_owner_receives_their_document(self, app: Any, db: FakeDB) -> None:
        _insert_profile(db, user_id=ALICE_ID, name="Alice Example", email="alice@example.com")
        with TestClient(app) as client:
            response = client.get("/api/v1/profile")
        assert response.status_code == 200
        body = response.json()
        assert body["contact"]["email"] == "alice@example.com"
        assert body["contact"]["full_name"] == "Alice Example"
        assert str(body["user_id"]) == str(ALICE_ID)
        assert "password_hash" not in body
        assert "token_version" not in body

    def test_missing_profile_is_not_found(self, app: Any) -> None:
        with TestClient(app) as client:
            response = client.get("/api/v1/profile")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_unauthenticated_is_rejected(self, app: Any) -> None:
        app.dependency_overrides.clear()
        with TestClient(app) as client:
            response = client.get("/api/v1/profile")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"

    def test_cannot_read_another_users_profile(self, app: Any, db: FakeDB) -> None:
        _insert_profile(db, user_id=ALICE_ID, name="Alice Example", email="alice@example.com")
        _insert_profile(db, user_id=BOB_ID, name="Bob Other", email="bob@example.com")
        with TestClient(app) as client:
            response = client.get("/api/v1/profile")
        assert response.status_code == 200
        assert response.json()["contact"]["email"] == "alice@example.com"
        assert response.json()["contact"]["full_name"] != "Bob Other"


class TestPutProfile:
    def test_update_persists_and_ignores_foreign_user_id(self, app: Any, db: FakeDB) -> None:
        _insert_profile(db, user_id=ALICE_ID, name="Alice Example", email="alice@example.com")
        _insert_profile(db, user_id=BOB_ID, name="Bob Other", email="bob@example.com")
        payload = {
            "user_id": str(BOB_ID),
            "contact": _contact(location="Berlin"),
            "headline": "Staff engineer",
            "completeness": 0.01,
            "experiences": [],
            "projects": [],
            "education": [{"institution": "TU Berlin"}],
            "skills": [{"name": "Python"}],
            "certifications": [],
        }
        with TestClient(app) as client:
            response = client.put("/api/v1/profile", json=payload)
            reloaded = client.get("/api/v1/profile")
        assert response.status_code == 200
        assert reloaded.status_code == 200
        body = response.json()
        reloaded_body = reloaded.json()
        assert str(body["user_id"]) == str(ALICE_ID)
        assert body["headline"] == "Staff engineer"
        assert body["contact"]["location"] == "Berlin"
        assert body["completeness"] == round(5 / 7, 4)
        assert reloaded_body["headline"] == "Staff engineer"
        assert reloaded_body["contact"]["location"] == "Berlin"
        assert reloaded_body["education"][0]["institution"] == "TU Berlin"
        assert reloaded_body["skills"][0]["name"] == "Python"

        stored = next(
            doc for doc in db[Collections.CAREER_PROFILES].docs if _same(doc["user_id"], ALICE_ID)
        )
        assert stored["headline"] == "Staff engineer"
        bob = next(
            doc for doc in db[Collections.CAREER_PROFILES].docs if _same(doc["user_id"], BOB_ID)
        )
        assert bob["contact"]["full_name"] == "Bob Other"

    def test_experience_and_project_entry_ids_remain_stable(self, app: Any, db: FakeDB) -> None:
        _insert_profile(
            db,
            user_id=ALICE_ID,
            name="Alice Example",
            email="alice@example.com",
            experiences=[
                {
                    "entry_id": ENTRY_ID,
                    "company": "Acme",
                    "title": "Engineer",
                    "dates": {"start": "2023-01-01", "end": None},
                    "raw_notes": "Rebuilt the payment retry path with Redis queues.",
                }
            ],
            projects=[{"entry_id": PROJECT_ID, "name": "ResuNet", "raw_notes": "side project"}],
        )
        payload = {
            "user_id": str(ALICE_ID),
            "contact": _contact(location="London"),
            "experiences": [
                {
                    "entry_id": ENTRY_ID,
                    "company": "Acme Corp",
                    "title": "Senior Engineer",
                    "dates": {"start": "2023-01-01"},
                    "raw_notes": "Rebuilt the payment retry path with Redis queues.",
                }
            ],
            "projects": [{"entry_id": PROJECT_ID, "name": "ResuNet", "raw_notes": "side project"}],
        }
        with TestClient(app) as client:
            response = client.put("/api/v1/profile", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["experiences"][0]["entry_id"] == ENTRY_ID
        assert body["experiences"][0]["title"] == "Senior Engineer"
        assert body["projects"][0]["entry_id"] == PROJECT_ID

    def test_profile_replacement_archives_removed_entry_bullets_atomically(
        self, app: Any, db: FakeDB
    ) -> None:
        _insert_profile(
            db,
            user_id=ALICE_ID,
            name="Alice Example",
            email="alice@example.com",
            experiences=[
                {
                    "entry_id": ENTRY_ID,
                    "company": "Acme",
                    "title": "Engineer",
                    "dates": {"start": "2023-01-01"},
                }
            ],
        )
        bullet = Bullet(
            user_id=str(ALICE_ID),
            profile_entry_id=ENTRY_ID,
            action="Reduced payment failures",
            metric="by 35% quarter over quarter",
            method="with idempotency checks",
            status=BulletStatus.APPROVED,
        )
        db[Collections.BULLETS].docs.append(bullet.to_mongo())
        payload = {
            "user_id": str(ALICE_ID),
            "contact": _contact(),
            "experiences": [],
            "projects": [],
        }

        with TestClient(app) as client:
            response = client.put("/api/v1/profile", json=payload)

        assert response.status_code == 200
        assert response.json()["experiences"] == []
        assert db[Collections.BULLETS].docs[0]["status"] == "archived"

    def test_invalid_payload_is_rejected(self, app: Any, db: FakeDB) -> None:
        _insert_profile(db, user_id=ALICE_ID, name="Alice Example", email="alice@example.com")
        with TestClient(app) as client:
            response = client.put("/api/v1/profile", json={"user_id": str(ALICE_ID)})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"

    def test_end_before_start_is_rejected(self, app: Any, db: FakeDB) -> None:
        _insert_profile(db, user_id=ALICE_ID, name="Alice Example", email="alice@example.com")
        payload = {
            "user_id": str(ALICE_ID),
            "contact": _contact(),
            "experiences": [
                {
                    "company": "Acme",
                    "title": "Engineer",
                    "dates": {"start": "2024-01-01", "end": "2023-01-01"},
                }
            ],
        }
        with TestClient(app) as client:
            response = client.put("/api/v1/profile", json=payload)
        assert response.status_code == 422


class TestExperiences:
    def test_create_and_delete(self, app: Any, db: FakeDB) -> None:
        _insert_profile(db, user_id=ALICE_ID, name="Alice Example", email="alice@example.com")
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/profile/experiences",
                json={
                    "company": "Acme",
                    "title": "Engineer",
                    "dates": {"start": "2023-01-01"},
                    "raw_notes": "Rebuilt retry logic and measured the drop in failures.",
                },
            )
            assert created.status_code == 201
            entry = created.json()
            assert entry["company"] == "Acme"
            assert entry["entry_id"]

            listed = client.get("/api/v1/profile")
            assert listed.status_code == 200
            assert len(listed.json()["experiences"]) == 1
            assert listed.json()["experiences"][0]["entry_id"] == entry["entry_id"]
            assert listed.json()["completeness"] == round(3 / 7, 4)

            deleted = client.delete(f"/api/v1/profile/experiences/{entry['entry_id']}")
            assert deleted.status_code == 204
            assert client.get("/api/v1/profile").json()["experiences"] == []

    def test_delete_archives_bullets_instead_of_removing_them(self, app: Any, db: FakeDB) -> None:
        _insert_profile(
            db,
            user_id=ALICE_ID,
            name="Alice Example",
            email="alice@example.com",
            experiences=[
                {
                    "entry_id": ENTRY_ID,
                    "company": "Acme",
                    "title": "Engineer",
                    "dates": {"start": "2023-01-01"},
                    "raw_notes": "Rebuilt retry logic and measured the drop in failures.",
                }
            ],
        )
        bullet = Bullet(
            user_id=str(ALICE_ID),
            profile_entry_id=ENTRY_ID,
            action="Rebuilt the payment retry pipeline",
            metric="cutting failed-charge rate from 4.2% to 0.8%",
            method="using Redis-backed idempotency keys",
            status=BulletStatus.APPROVED,
        )
        db[Collections.BULLETS].docs.append(bullet.to_mongo())

        with TestClient(app) as client:
            response = client.delete(f"/api/v1/profile/experiences/{ENTRY_ID}")
        assert response.status_code == 204

        stored = db[Collections.BULLETS].docs[0]
        assert stored["status"] == BulletStatus.ARCHIVED.value
        assert stored["action"] == "Rebuilt the payment retry pipeline"

    def test_delete_unknown_entry_is_not_found(self, app: Any, db: FakeDB) -> None:
        _insert_profile(db, user_id=ALICE_ID, name="Alice Example", email="alice@example.com")
        with TestClient(app) as client:
            response = client.delete("/api/v1/profile/experiences/999999999999999999999999")
        assert response.status_code == 404

    def test_cannot_mutate_another_users_experiences(self, app: Any, db: FakeDB) -> None:
        _insert_profile(
            db,
            user_id=BOB_ID,
            name="Bob Other",
            email="bob@example.com",
            experiences=[
                {
                    "entry_id": ENTRY_ID,
                    "company": "Secret Co",
                    "title": "Lead",
                    "dates": {"start": "2020-01-01"},
                }
            ],
        )
        _insert_profile(db, user_id=ALICE_ID, name="Alice Example", email="alice@example.com")
        with TestClient(app) as client:
            deleted = client.delete(f"/api/v1/profile/experiences/{ENTRY_ID}")
            created = client.post(
                "/api/v1/profile/experiences",
                json={
                    "company": "Acme",
                    "title": "Engineer",
                    "dates": {"start": "2023-01-01"},
                },
            )
        assert deleted.status_code == 404
        assert created.status_code == 201
        bob = next(
            doc for doc in db[Collections.CAREER_PROFILES].docs if _same(doc["user_id"], BOB_ID)
        )
        assert bob["experiences"][0]["company"] == "Secret Co"
        assert len(bob["experiences"]) == 1
