from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from bson import ObjectId

from worker.tasks.submit import (
    _approved_fingerprint,
    _claim_submission,
    _enqueue_deferred,
)


class QueuedApplicationCollection:
    def __init__(self, status: str = "queued") -> None:
        self.document: dict[str, Any] = {
            "_id": ObjectId(),
            "status": status,
            "updated_at": datetime.now(UTC),
        }
        self.claim_calls = 0

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        return_document: Any,
    ) -> dict[str, Any] | None:
        self.claim_calls += 1
        if query != {"_id": self.document["_id"], "status": "queued"}:
            raise AssertionError("claim must be limited to this queued application")
        if self.document["status"] != "queued":
            return None
        self.document.update(update["$set"])
        return self.document.copy()


class FakeDatabase:
    def __init__(self, applications: QueuedApplicationCollection) -> None:
        self.applications = applications


class FakeRedisQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.kwargs: dict[str, Any] | None = None

    async def enqueue_job(self, task_name: str, **kwargs: Any) -> object | None:
        self.kwargs = {"task_name": task_name, **kwargs}
        if self.fail:
            raise ConnectionError("offline")
        return object()


@pytest.mark.asyncio
async def test_claim_changes_queued_to_submitting_once() -> None:
    applications = QueuedApplicationCollection()
    database = FakeDatabase(applications)

    first = await _claim_submission(database, applications.document["_id"])
    second = await _claim_submission(database, applications.document["_id"])

    assert first is not None
    assert first["status"] == "submitting"
    assert second is None
    assert applications.claim_calls == 2


@pytest.mark.asyncio
async def test_claim_refuses_nonqueued_status() -> None:
    applications = QueuedApplicationCollection(status="awaiting_approval")

    result = await _claim_submission(FakeDatabase(applications), applications.document["_id"])

    assert result is None
    assert applications.document["status"] == "awaiting_approval"


def test_approval_fingerprint_requires_review_attempt() -> None:
    assert _approved_fingerprint({"attempts": []}) is None
    assert _approved_fingerprint(
        {"attempts": [{"failed_step": "await_approval", "field_mappings": []}]}
    ) is None


def test_approval_fingerprint_uses_latest_reviewable_form() -> None:
    application = {
        "attempts": [
            {
                "failed_step": "await_approval",
                "field_mappings": [{"label": "Old field", "value_preview": "old"}],
            },
            {
                "failed_step": "await_approval",
                "field_mappings": [
                    {"label": "Name", "value_preview": "Alex"},
                    {"label": "Email", "value_preview": "a***@example.com"},
                ],
            },
        ]
    }

    assert _approved_fingerprint(application) == frozenset(
        {("Name", "Alex"), ("Email", "a***@example.com")}
    )


@pytest.mark.asyncio
async def test_deferred_retry_is_enqueued_with_owner_and_approval_state() -> None:
    redis = FakeRedisQueue()

    queued = await _enqueue_deferred(
        {"redis": redis}, "application-id", "user-id", True, 3, 1.2
    )

    assert queued is True
    assert redis.kwargs is not None
    assert redis.kwargs["task_name"] == "submit_application"
    assert redis.kwargs["application_id"] == "application-id"
    assert redis.kwargs["user_id"] == "user-id"
    assert redis.kwargs["approved"] is True
    assert redis.kwargs["attempt_number"] == 3
    assert redis.kwargs["_defer_by"] == 2
    assert "deferred:" in redis.kwargs["_job_id"]


@pytest.mark.asyncio
async def test_deferred_retry_reports_queue_failure_without_raising() -> None:
    redis = FakeRedisQueue(fail=True)

    queued = await _enqueue_deferred(
        {"redis": redis}, "application-id", "user-id", False, 1, 2
    )

    assert queued is False
