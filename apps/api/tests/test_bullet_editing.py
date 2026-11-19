"""Tests for manual bullet editing (PATCH) and re-approval (POST approve).

An edit re-validates the XYZ shape and resets the bullet to DRAFT, so an
edited bullet cannot reach a render on an old APPROVED stamp -- render
refuses any section bullet that is not APPROVED. Re-approval re-checks XYZ
at approval time.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from bson import ObjectId

from app.api.v1.routes.resumes import approve_bullet, update_bullet
from app.core.errors import NotFoundError, ValidationFailedError
from app.db.mongo import Collections
from app.models.bullet import BulletUpdate
from app.models.profile import ContactBlock
from app.models.resume import Resume
from app.models.user import User, UserRole, UserStatus

USER_ID = ObjectId("111111111111111111111111")
OTHER_USER_ID = ObjectId("999999999999999999999999")
RESUME_ID = ObjectId("222222222222222222222222")
BULLET_ID = ObjectId("444444444444444444444444")
OTHER_BULLET_ID = ObjectId("555555555555555555555555")


def _same(left: object, right: object) -> bool:
    return str(left) == str(right)


def _matches(doc: dict[str, Any], filt: dict[str, Any]) -> bool:
    for key, expected in filt.items():
        actual = doc.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if not any(_same(actual, candidate) for candidate in expected["$in"]):
                return False
        elif not _same(actual, expected):
            return False
    return True


class _FakeCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    async def to_list(self, length: int) -> list[dict[str, Any]]:
        return self._docs[:length]


class _FakeCollection:
    def __init__(self, docs: list[dict[str, Any]] | None = None) -> None:
        self.docs: list[dict[str, Any]] = docs or []

    def find(self, filt: dict[str, Any], sort: object = None) -> _FakeCursor:
        rows = [doc for doc in self.docs if _matches(doc, filt)]
        if sort:
            rows = sorted(rows, key=lambda d: d.get("created_at") or datetime.min)
        return _FakeCursor(rows)

    async def find_one(self, filt: dict[str, Any]) -> dict[str, Any] | None:
        for doc in self.docs:
            if _matches(doc, filt):
                return doc
        return None

    async def update_one(self, filt: dict[str, Any], update: dict[str, Any]) -> None:
        for doc in self.docs:
            if _matches(doc, filt):
                for key, value in update.get("$set", {}).items():
                    doc[key] = value
                return


def _user(user_id: ObjectId = USER_ID) -> User:
    return User(
        id=str(user_id),
        email="alice@example.com",
        password_hash="argon2-not-a-real-hash",  # noqa: S106 - test fixture, not a secret
        full_name="Alice Example",
        status=UserStatus.ACTIVE,
        roles=[UserRole.USER],
        token_version=1,
    )


def _resume_doc() -> dict[str, Any]:
    resume = Resume(
        id=str(RESUME_ID),
        user_id=str(USER_ID),
        title="Test Resume",
        contact=ContactBlock(full_name="Alice Example", email="alice@example.com"),
        sections=[],
    )
    doc = resume.to_mongo()
    doc["_id"] = RESUME_ID
    return doc


def _bullet_doc(
    *,
    bullet_id: ObjectId = BULLET_ID,
    user_id: ObjectId = USER_ID,
    status: str = "approved",
    metric: str = "student usage from 50 to 200 per month",
    method: str = "by optimizing the borrowing system",
) -> dict[str, Any]:
    now = datetime(2026, 11, 1, 5, 0, tzinfo=UTC)
    return {
        "_id": bullet_id,
        "user_id": user_id,
        "profile_entry_id": str(ObjectId()),
        "action": "Increased",
        "metric": metric,
        "method": method,
        "status": status,
        "source": "agent_generated",
        "violations": [],
        "thread_id": "thread_1",
        "created_at": now,
        "updated_at": now,
    }


@pytest.fixture
def collections(monkeypatch: pytest.MonkeyPatch) -> dict[Collections, _FakeCollection]:
    mapping = {
        Collections.RESUMES: _FakeCollection([_resume_doc()]),
        Collections.AGENT_THREADS: _FakeCollection(
            [{"thread_id": "thread_1", "resume_id": RESUME_ID, "user_id": USER_ID}]
        ),
        Collections.BULLETS: _FakeCollection(
            [_bullet_doc(), _bullet_doc(bullet_id=OTHER_BULLET_ID, user_id=OTHER_USER_ID)]
        ),
    }
    monkeypatch.setattr("app.api.v1.routes.resumes.collection", lambda name: mapping[name])
    # fetch_owned (used by _load_resume_doc and the bullet lookup) resolves
    # `collection` through app.api.deps, not through the routes module.
    monkeypatch.setattr("app.api.deps.collection", lambda name: mapping[name])
    return mapping


def test_edit_updates_fields_and_resets_to_draft(collections: dict) -> None:
    """A valid edit stores the new text and drops the APPROVED stamp."""
    result = asyncio.run(
        update_bullet(
            str(RESUME_ID),
            str(BULLET_ID),
            BulletUpdate(metric="late returns fell by 30%"),
            _user(),
        )
    )
    assert result.metric == "late returns fell by 30%"
    assert result.status == "draft"
    assert result.action == "Increased"  # unset fields are left alone


def test_edited_bullet_needs_reapproval(collections: dict) -> None:
    """Edit -> DRAFT, approve -> APPROVED again; the round trip is the gate
    that keeps an edited bullet out of a render until it is re-checked."""
    asyncio.run(
        update_bullet(
            str(RESUME_ID),
            str(BULLET_ID),
            BulletUpdate(method="by rebuilding the borrowing queue"),
            _user(),
        )
    )
    edited = asyncio.run(approve_bullet(str(RESUME_ID), str(BULLET_ID), _user()))
    assert edited.status == "approved"


def test_invalid_edit_is_rejected_and_nothing_stored(collections: dict) -> None:
    """An edit that breaks XYZ (metric with no number) is a 422 and the
    stored bullet is untouched."""
    with pytest.raises(ValidationFailedError):
        asyncio.run(
            update_bullet(
                str(RESUME_ID),
                str(BULLET_ID),
                BulletUpdate(metric="making things much better"),
                _user(),
            )
        )
    stored = collections[Collections.BULLETS].docs[0]
    assert stored["metric"] == "student usage from 50 to 200 per month"
    assert stored["status"] == "approved"


def test_approve_rejects_noncompliant_bullet(collections: dict) -> None:
    """Approval re-checks XYZ: a bullet whose metric carries no number
    cannot be approved into a render, even if it was stored that way."""
    collections[Collections.BULLETS].docs[0]["status"] = "draft"
    collections[Collections.BULLETS].docs[0]["metric"] = "making things better"
    with pytest.raises(ValidationFailedError):
        asyncio.run(approve_bullet(str(RESUME_ID), str(BULLET_ID), _user()))
    assert collections[Collections.BULLETS].docs[0]["status"] == "draft"


def test_other_users_bullet_is_not_found(collections: dict) -> None:
    """Ownership is part of the lookup: another user's bullet is
    indistinguishable from a missing one."""
    with pytest.raises(NotFoundError):
        asyncio.run(
            update_bullet(
                str(RESUME_ID),
                str(OTHER_BULLET_ID),
                BulletUpdate(metric="late returns fell by 30%"),
                _user(),
            )
        )
