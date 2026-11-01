"""Tests for the resume bullets endpoint's unassigned pile.

Regression test for the 2026-11-01 live failure: generation saved bullets
against a thread, but the bullets endpoint only returned bullets already in
a resume section, so generated bullets were invisible in the workspace even
though they were saved. The endpoint must surface thread bullets as
unassigned, while rendering (which uses _ordered_bullets, not this endpoint)
must still only see section bullets.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from bson import ObjectId

from app.api.v1.routes.resumes import _resume_thread_bullets
from app.db.mongo import Collections
from app.models.profile import ContactBlock
from app.models.resume import Resume
from app.models.user import User, UserRole, UserStatus

USER_ID = ObjectId("111111111111111111111111")
RESUME_ID = ObjectId("222222222222222222222222")
OTHER_RESUME_ID = ObjectId("333333333333333333333333")


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


def _user() -> User:
    return User(
        id=str(USER_ID),
        email="alice@example.com",
        password_hash="argon2-not-a-real-hash",  # noqa: S106 - test fixture, not a secret
        full_name="Alice Example",
        status=UserStatus.ACTIVE,
        roles=[UserRole.USER],
        token_version=1,
    )


def _resume() -> Resume:
    return Resume(
        id=str(RESUME_ID),
        user_id=str(USER_ID),
        title="Test Resume",
        contact=ContactBlock(full_name="Alice Example", email="alice@example.com"),
        sections=[],
    )


def _bullet_doc(
    *, bullet_id: ObjectId, thread_id: str, created: datetime
) -> dict[str, Any]:
    return {
        "_id": bullet_id,
        "user_id": USER_ID,
        "profile_entry_id": str(ObjectId()),
        "action": "Increased",
        "metric": "student usage from 50 to 200 per month",
        "method": "by optimizing the borrowing system",
        "status": "approved",
        "source": "agent_generated",
        "violations": [],
        "thread_id": thread_id,
        "created_at": created,
        "updated_at": created,
    }


@pytest.fixture
def collections(monkeypatch: pytest.MonkeyPatch) -> dict[Collections, _FakeCollection]:
    threads = _FakeCollection(
        [
            {
                "thread_id": "thread_1",
                "resume_id": RESUME_ID,
                "user_id": USER_ID,
            },
            {
                "thread_id": "thread_other",
                "resume_id": OTHER_RESUME_ID,
                "user_id": USER_ID,
            },
        ]
    )
    bullets = _FakeCollection(
        [
            _bullet_doc(
                bullet_id=ObjectId("444444444444444444444444"),
                thread_id="thread_1",
                created=datetime(2026, 11, 1, 5, 0, tzinfo=UTC),
            ),
            _bullet_doc(
                bullet_id=ObjectId("555555555555555555555555"),
                thread_id="thread_1",
                created=datetime(2026, 11, 1, 6, 0, tzinfo=UTC),
            ),
            _bullet_doc(
                bullet_id=ObjectId("666666666666666666666666"),
                thread_id="thread_other",
                created=datetime(2026, 11, 1, 7, 0, tzinfo=UTC),
            ),
        ]
    )
    mapping = {Collections.AGENT_THREADS: threads, Collections.BULLETS: bullets}
    monkeypatch.setattr(
        "app.api.v1.routes.resumes.collection", lambda name: mapping[name]
    )
    return mapping


def test_thread_bullets_surface_as_unassigned(collections: dict) -> None:
    result = asyncio.run(
        _resume_thread_bullets(_resume(), _user(), assigned_ids=set())
    )
    ids = [str(b.id) for b in result]
    assert ids == ["444444444444444444444444", "555555555555555555555555"]


def test_assigned_bullets_are_excluded(collections: dict) -> None:
    result = asyncio.run(
        _resume_thread_bullets(
            _resume(), _user(), assigned_ids={"444444444444444444444444"}
        )
    )
    assert [str(b.id) for b in result] == ["555555555555555555555555"]
