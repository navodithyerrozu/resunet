"""Resume CRUD tests using an in-memory Mongo collection substitute."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.db.mongo import Collections
from app.main import create_app
from app.models.profile import CareerProfile, ContactBlock, ExperienceEntry
from app.models.user import User, UserRole, UserStatus

USER_ID = ObjectId("111111111111111111111111")
OTHER_ID = ObjectId("222222222222222222222222")
ENTRY_ID = "333333333333333333333333"
BULLET_ID = ObjectId("444444444444444444444444")


def _matches(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if not any(str(actual) == str(candidate) for candidate in expected["$in"]):
                return False
        elif isinstance(expected, dict) and "$ne" in expected:
            if actual == expected["$ne"]:
                return False
        elif str(actual) != str(expected) and actual != expected:
            return False
    return True


class _Result:
    def __init__(self, matched_count: int, inserted_id: ObjectId | None = None) -> None:
        self.matched_count = matched_count
        self.inserted_id = inserted_id


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def sort(self, field: str, direction: int) -> _Cursor:
        self.docs.sort(key=lambda doc: doc.get(field), reverse=direction < 0)
        return self

    def skip(self, offset: int) -> _Cursor:
        self.docs = self.docs[offset:]
        return self

    def limit(self, limit: int) -> _Cursor:
        self.docs = self.docs[:limit]
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return deepcopy(self.docs[:length]) if length is not None else deepcopy(self.docs)


class _Collection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def find_one(self, query: dict[str, Any], **_: object) -> dict[str, Any] | None:
        return next((deepcopy(doc) for doc in self.docs if _matches(doc, query)), None)

    def find(
        self, query: dict[str, Any], projection: dict[str, Any] | None = None, **_: object
    ) -> _Cursor:
        rows = [deepcopy(doc) for doc in self.docs if _matches(doc, query)]
        if projection and projection.get("latex_source") == 0:
            for row in rows:
                row.pop("latex_source", None)
        return _Cursor(rows)

    async def count_documents(self, query: dict[str, Any]) -> int:
        return sum(_matches(doc, query) for doc in self.docs)

    async def insert_one(self, doc: dict[str, Any], **_: object) -> _Result:
        inserted = deepcopy(doc)
        inserted.setdefault("_id", ObjectId())
        self.docs.append(inserted)
        return _Result(1, inserted["_id"])

    async def find_one_and_update(
        self, query: dict[str, Any], update: dict[str, Any], **_: object
    ) -> dict[str, Any] | None:
        for doc in self.docs:
            if _matches(doc, query):
                doc.update(deepcopy(update.get("$set", {})))
                return deepcopy(doc)
        return None

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], **_: object
    ) -> _Result:
        for doc in self.docs:
            if _matches(doc, query):
                doc.update(deepcopy(update.get("$set", {})))
                for key, value in update.get("$inc", {}).items():
                    doc[key] = doc.get(key, 0) + value
                for key, value in update.get("$addToSet", {}).items():
                    target: Any = doc
                    parts = key.split(".")
                    for part in parts[:-1]:
                        target = target[int(part)] if isinstance(target, list) else target[part]
                    items = target.setdefault(parts[-1], [])
                    if value not in items:
                        items.append(value)
                return _Result(1)
        return _Result(0)


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


class _DB:
    def __init__(self) -> None:
        self.collections = {name: _Collection() for name in Collections}

    def collection(self, name: Collections) -> _Collection:
        return self.collections[name]


@pytest.fixture
def resume_env(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, _DB]:
    db = _DB()
    monkeypatch.setattr("app.main.connect", _noop)
    monkeypatch.setattr("app.main.disconnect", _noop)
    monkeypatch.setattr("app.main.connect_queues", _noop)
    monkeypatch.setattr("app.main.disconnect_queues", _noop)
    monkeypatch.setattr("app.api.deps.collection", db.collection)
    monkeypatch.setattr("app.api.v1.routes.resumes.collection", db.collection)
    monkeypatch.setattr("app.api.v1.routes.resumes.get_client", lambda: _Client())

    async def _enqueue_render(**_: object) -> str:
        return "render:test-job"

    monkeypatch.setattr("app.api.v1.routes.resumes.enqueue_render", _enqueue_render)
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _user(USER_ID)
    return app, db


async def _noop() -> None:
    return None


def _user(user_id: ObjectId) -> User:
    return User(
        id=str(user_id),
        email=f"{user_id}@example.com",
        password_hash="not-used-by-these-tests",  # noqa: S106 - synthetic test fixture
        full_name="Resume Tester",
        status=UserStatus.ACTIVE,
        roles=[UserRole.USER],
        token_version=1,
    )


def _insert_profile(db: _DB, user_id: ObjectId = USER_ID) -> None:
    profile = CareerProfile(
        user_id=str(user_id),
        contact=ContactBlock(full_name="Resume Tester", email="resume@example.com"),
        headline="Backend engineer",
        summary="Builds reliable services.",
        experiences=[
            ExperienceEntry(
                entry_id=ENTRY_ID,
                company="Acme",
                title="Engineer",
                dates={"start": "2022-01-01"},
            )
        ],
    )
    doc = profile.to_mongo()
    doc["user_id"] = user_id
    db.collection(Collections.CAREER_PROFILES).docs.append(doc)


class TestResumeCrud:
    def test_profile_to_resume_http_lifecycle(
        self, resume_env: tuple[Any, _DB], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exercise one authenticated profile-to-resume journey across HTTP requests."""
        app, db = resume_env
        _insert_profile(db)
        monkeypatch.setattr("app.api.v1.routes.profile.collection", db.collection)

        with TestClient(app) as client:
            profile = client.get("/api/v1/profile")
            assert profile.status_code == 200
            assert profile.json()["contact"]["email"] == "resume@example.com"

            created = client.post("/api/v1/resumes", json={"title": "Journey role"})
            assert created.status_code == 201
            resume_id = created.json()["id"]
            assert created.json()["contact"]["email"] == profile.json()["contact"]["email"]

            reloaded = client.get(f"/api/v1/resumes/{resume_id}")
            assert reloaded.status_code == 200
            assert reloaded.json()["sections"][1]["entry_ids"] == [ENTRY_ID]

            updated = client.patch(
                f"/api/v1/resumes/{resume_id}", json={"title": "Journey role updated"}
            )
            assert updated.status_code == 200
            assert updated.json()["title"] == "Journey role updated"

            archived = client.delete(f"/api/v1/resumes/{resume_id}")
            assert archived.status_code == 204
            assert client.get(f"/api/v1/resumes/{resume_id}").json()["status"] == "archived"

    def test_create_from_profile_then_list_get_and_update(
        self, resume_env: tuple[Any, _DB]
    ) -> None:
        app, db = resume_env
        _insert_profile(db)
        with TestClient(app) as client:
            created = client.post("/api/v1/resumes", json={"title": "Backend role"})
            assert created.status_code == 201
            resume = created.json()
            stored_resume = db.collection(Collections.RESUMES).docs[0]
            assert stored_resume["user_id"] == USER_ID
            assert resume["contact"]["email"] == "resume@example.com"
            assert resume["sections"][1]["entry_ids"] == [ENTRY_ID]

            listed = client.get("/api/v1/resumes")
            assert listed.status_code == 200
            assert listed.json()["total"] == 1
            assert listed.json()["items"][0]["id"] == resume["id"]

            loaded = client.get(f"/api/v1/resumes/{resume['id']}")
            assert loaded.status_code == 200
            assert loaded.json()["title"] == "Backend role"

            versions = db.collection(Collections.RESUME_VERSIONS)
            version = {
                "_id": ObjectId(),
                "resume_id": ObjectId(resume["id"]),
                "user_id": USER_ID,
                "version": 1,
                "template": "ats_classic",
                "contact": resume["contact"],
                "summary": resume["summary"],
                "sections": [],
                "skills": [],
                "education": [],
                "certifications": [],
                "latex_source": "private render source",
                "created_at": resume["created_at"],
                "updated_at": resume["updated_at"],
            }
            versions.docs.append(version)
            history = client.get(f"/api/v1/resumes/{resume['id']}/versions")
            assert history.status_code == 200
            assert [item["version"] for item in history.json()["items"]] == [1]
            assert history.json()["items"][0]["id"] == str(version["_id"])
            assert "latex_source" not in history.json()["items"][0]

            updated = client.patch(
                f"/api/v1/resumes/{resume['id']}",
                json={"title": "Updated title", "summary": None},
            )
            assert updated.status_code == 200
            assert updated.json()["title"] == "Updated title"
            assert updated.json()["summary"] is None

    def test_resume_isolation_and_archive(self, resume_env: tuple[Any, _DB]) -> None:
        app, db = resume_env
        _insert_profile(db)
        other = CareerProfile(
            user_id=str(OTHER_ID),
            contact=ContactBlock(full_name="Other", email="other@example.com"),
        )
        other_doc = other.to_mongo()
        other_doc["user_id"] = OTHER_ID
        db.collection(Collections.CAREER_PROFILES).docs.append(other_doc)
        with TestClient(app) as client:
            own = client.post("/api/v1/resumes", json={"title": "Mine"}).json()
            # The other user's resume is inserted directly to exercise the owner filter.
            foreign = {
                "_id": ObjectId(),
                "user_id": OTHER_ID,
                "title": "Private",
                "status": "draft",
                "template": "ats_classic",
                "contact": {"full_name": "Other", "email": "other@example.com"},
                "sections": [],
                "current_version": 0,
                "created_at": own["created_at"],
                "updated_at": own["updated_at"],
            }
            db.collection(Collections.RESUMES).docs.append(foreign)
            assert client.get(f"/api/v1/resumes/{foreign['_id']}").status_code == 404

            assert client.delete(f"/api/v1/resumes/{own['id']}").status_code == 204
            assert client.get(f"/api/v1/resumes/{own['id']}").json()["status"] == "archived"
            patched = client.patch(f"/api/v1/resumes/{own['id']}", json={"title": "No"})
            assert patched.status_code == 404
            assert client.delete(f"/api/v1/resumes/{own['id']}").status_code == 204

    def test_missing_profile_cannot_create_resume(self, resume_env: tuple[Any, _DB]) -> None:
        app, _ = resume_env
        with TestClient(app) as client:
            response = client.post("/api/v1/resumes", json={"title": "Needs profile"})
        assert response.status_code == 404

    def test_accepting_generated_proposal_attaches_it_to_resume(
        self, resume_env: tuple[Any, _DB]
    ) -> None:
        app, db = resume_env
        _insert_profile(db)
        with TestClient(app) as client:
            resume_response = client.post("/api/v1/resumes", json={"title": "Backend role"})
            resume = resume_response.json()
            thread_id = "gen_decision_test"
            db.collection(Collections.AGENT_THREADS).docs.append(
                {
                    "_id": ObjectId(),
                    "thread_id": thread_id,
                    "user_id": USER_ID,
                    "resume_id": ObjectId(resume["id"]),
                    "profile_entry_id": ENTRY_ID,
                    "kind": "resume_generation",
                    "status": "completed",
                }
            )
            db.collection(Collections.BULLETS).docs.append(
                {
                    "_id": BULLET_ID,
                    "user_id": USER_ID,
                    "profile_entry_id": ObjectId(ENTRY_ID),
                    "action": "Reduced payment failures",
                    "metric": "by 35% quarter over quarter",
                    "method": "by adding idempotency checks",
                    "skills": ["Python"],
                    "status": "approved",
                    "source": "agent_generated",
                    "violations": [],
                    "thread_id": thread_id,
                    "repair_attempts": 1,
                    "times_used": 0,
                    "created_at": resume["created_at"],
                    "updated_at": resume["updated_at"],
                }
            )

            response = client.post(
                f"/api/v1/resumes/generate/{thread_id}/proposals/{BULLET_ID}/decision",
                json={"action": "accept"},
            )

        assert response.status_code == 200
        updated_resume = db.collection(Collections.RESUMES).docs[0]
        experience_section = next(
            section for section in updated_resume["sections"] if section["kind"] == "experience"
        )
        assert BULLET_ID in experience_section["bullet_ids"]

    def test_flagged_proposal_requires_compliant_revision(
        self, resume_env: tuple[Any, _DB]
    ) -> None:
        app, db = resume_env
        _insert_profile(db)
        with TestClient(app) as client:
            resume = client.post("/api/v1/resumes", json={"title": "Backend role"}).json()
            thread_id = "gen_revision_test"
            db.collection(Collections.AGENT_THREADS).docs.append(
                {
                    "_id": ObjectId(),
                    "thread_id": thread_id,
                    "user_id": USER_ID,
                    "resume_id": ObjectId(resume["id"]),
                    "profile_entry_id": ENTRY_ID,
                    "kind": "resume_generation",
                    "status": "completed",
                }
            )
            db.collection(Collections.BULLETS).docs.append(
                {
                    "_id": BULLET_ID,
                    "user_id": USER_ID,
                    "profile_entry_id": ObjectId(ENTRY_ID),
                    "action": "Built a reporting pipeline",
                    "metric": "faster",
                    "method": "using scheduled data jobs",
                    "skills": [],
                    "status": "needs_review",
                    "source": "agent_generated",
                    "violations": [],
                    "thread_id": thread_id,
                    "repair_attempts": 3,
                    "times_used": 0,
                    "created_at": resume["created_at"],
                    "updated_at": resume["updated_at"],
                }
            )
            refused = client.post(
                f"/api/v1/resumes/generate/{thread_id}/proposals/{BULLET_ID}/decision",
                json={"action": "accept"},
            )
            revised = client.post(
                f"/api/v1/resumes/generate/{thread_id}/proposals/{BULLET_ID}/decision",
                json={
                    "action": "revise",
                    "draft": {
                        "action": "Built a reporting pipeline",
                        "metric": "cut monthly close time by 12 hours",
                        "method": "using scheduled data jobs",
                        "skills": ["Python"],
                    },
                },
            )

        assert refused.status_code == 409
        assert revised.status_code == 200
        assert revised.json()["status"] == "approved"
        assert revised.json()["metric"] == "cut monthly close time by 12 hours"

    def test_generation_session_restores_after_reload(
        self, resume_env: tuple[Any, _DB], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app, db = resume_env
        _insert_profile(db)
        thread_id = "gen_restore_test"
        with TestClient(app) as client:
            resume = client.post("/api/v1/resumes", json={"title": "Backend role"}).json()
            db.collection(Collections.RESUMES).docs[0]["thread_id"] = thread_id
            db.collection(Collections.AGENT_THREADS).docs.append(
                {
                    "_id": ObjectId(),
                    "thread_id": thread_id,
                    "user_id": USER_ID,
                    "resume_id": ObjectId(resume["id"]),
                    "profile_entry_id": ENTRY_ID,
                    "kind": "resume_generation",
                    "status": "awaiting_user",
                }
            )
            graph_state = {
                "status": "awaiting_user",
                "pending_question": "How much time did this save?",
                "messages": [],
                "completed_bullets": [],
                "flagged_bullets": [],
            }

            class Graph:
                async def aget_state(self, config: dict[str, Any]) -> Any:
                    assert config["configurable"]["thread_id"] == thread_id
                    return SimpleNamespace(values=graph_state)

            async def get_graph() -> Graph:
                return Graph()

            monkeypatch.setattr("app.agents.resume.get_compiled_graph", get_graph)
            restored = client.get(f"/api/v1/resumes/{resume['id']}/generation")
            graph_state.update(
                {
                    "status": "done",
                    "pending_question": None,
                    "completed_bullets": [
                        {
                            "draft": {
                                "action": "Reduced payment failures",
                                "metric": "by 35% quarter over quarter",
                                "method": "by adding idempotency checks",
                                "skills": ["Python"],
                            },
                            "text": (
                                "Reduced payment failures by 35% quarter over quarter "
                                "by adding idempotency checks"
                            ),
                            "violations": [],
                            "repair_attempts": 1,
                            "accepted": True,
                        }
                    ],
                    "persisted_bullet_ids": [str(BULLET_ID)],
                }
            )
            db.collection(Collections.AGENT_THREADS).docs[0]["status"] = "completed"
            db.collection(Collections.BULLETS).docs.append(
                {
                    "_id": BULLET_ID,
                    "user_id": USER_ID,
                    "profile_entry_id": ObjectId(ENTRY_ID),
                    "action": "Reduced payment failures",
                    "metric": "by 35% quarter over quarter",
                    "method": "by adding idempotency checks",
                    "skills": ["Python"],
                    "status": "archived",
                    "source": "agent_generated",
                    "violations": [],
                    "thread_id": thread_id,
                    "review_decision": "rejected",
                    "repair_attempts": 1,
                    "times_used": 0,
                    "created_at": resume["created_at"],
                    "updated_at": resume["updated_at"],
                }
            )
            restored_completed = client.get(f"/api/v1/resumes/{resume['id']}/generation")

        assert restored.status_code == 200
        assert restored.json()["threadId"] == thread_id
        assert restored.json()["question"] == "How much time did this save?"
        assert restored.json()["needsInput"] is True
        assert restored_completed.status_code == 200
        assert restored_completed.json()["proposals"][0]["decision"] == "rejected"

    def test_render_freezes_snapshot_and_tracks_worker_job(
        self, resume_env: tuple[Any, _DB]
    ) -> None:
        app, db = resume_env
        _insert_profile(db)
        with TestClient(app) as client:
            created = client.post("/api/v1/resumes", json={"title": "Render me"})
            resume = created.json()
            bullet = {
                "_id": BULLET_ID,
                "user_id": USER_ID,
                "profile_entry_id": ObjectId(ENTRY_ID),
                "action": "Reduced payment failures",
                "metric": "from 4% to 1% across 10k daily charges",
                "method": "using Redis-backed retries",
                "skills": ["Python", "Redis"],
                "status": "approved",
                "source": "user_authored",
                "violations": [],
                "repair_attempts": 0,
                "times_used": 0,
            }
            db.collection(Collections.BULLETS).docs.append(bullet)
            sections = resume["sections"]
            sections[1]["bullet_ids"] = [str(BULLET_ID)]
            patched = client.patch(f"/api/v1/resumes/{resume['id']}", json={"sections": sections})
            assert patched.status_code == 200
            attached = client.get(f"/api/v1/resumes/{resume['id']}/bullets")
            assert attached.status_code == 200
            assert attached.json()["items"][0]["id"] == str(BULLET_ID)

            response = client.post(
                f"/api/v1/resumes/{resume['id']}/render",
                json={"changeNote": "Initial render"},
            )
            assert response.status_code == 202
            job = response.json()
            assert job["status"] == "queued"
            assert job["arq_job_id"] == "render:test-job"
            assert job["resume_id"] == resume["id"]

            stored_resume = db.collection(Collections.RESUMES).docs[0]
            assert stored_resume["current_version"] == 1
            version = db.collection(Collections.RESUME_VERSIONS).docs[0]
            assert version["version"] == 1
            assert version["resume_id"] == ObjectId(resume["id"])
            assert version["user_id"] == USER_ID
            assert version["change_note"] == "Initial render"
            assert version["sections"][0]["entries"][0]["heading"] == "Engineer, Acme"
            frozen_bullet = version["sections"][0]["entries"][0]["bullets"][0]
            assert frozen_bullet["bullet_id"] == BULLET_ID
            assert frozen_bullet["text"] == (
                bullet["action"] + " " + bullet["metric"] + " " + bullet["method"] + "."
            )

            polled = client.get(f"/api/v1/resumes/render-jobs/{job['id']}")
            assert polled.status_code == 200
            assert polled.json()["status"] == "queued"

    def test_queue_failure_is_recorded_and_never_reported_as_queued(
        self,
        resume_env: tuple[Any, _DB],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        app, db = resume_env
        _insert_profile(db)

        async def fail_to_enqueue(**_: object) -> str:
            raise ConnectionError("redis unavailable")

        monkeypatch.setattr("app.api.v1.routes.resumes.enqueue_render", fail_to_enqueue)
        with TestClient(app) as client:
            created = client.post("/api/v1/resumes", json={"title": "Queue error"})
            response = client.post(f"/api/v1/resumes/{created.json()['id']}/render", json={})

        assert response.status_code == 503
        assert db.collection(Collections.RESUMES).docs[0]["current_version"] == 1
        failed_job = db.collection(Collections.RENDER_JOBS).docs[0]
        assert failed_job["status"] == "failed"
        assert "could not be submitted" in failed_job["error_message"]
