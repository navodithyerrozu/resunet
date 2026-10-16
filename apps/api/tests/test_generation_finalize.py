"""Persisted generation proposal IDs and BSON relationship fields."""

from __future__ import annotations

import asyncio
from importlib import import_module
from typing import Any

import pytest
from bson import ObjectId

from app.agents.resume.nodes.finalize import finalize
from app.db.mongo import Collections

USER_ID = ObjectId("111111111111111111111111")
ENTRY_ID = ObjectId("222222222222222222222222")


class FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []
        self.update: dict[str, Any] | None = None

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return next(
            (
                doc
                for doc in self.docs
                if all(doc.get(key) == value for key, value in query.items())
            ),
            None,
        )

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], *, upsert: bool = False
    ) -> None:
        existing = next(
            (
                doc
                for doc in self.docs
                if all(doc.get(key) == value for key, value in query.items())
            ),
            None,
        )
        if existing is None and upsert:
            self.docs.append(update["$setOnInsert"])


def test_finalize_returns_persisted_bullet_ids_and_bson_refs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bullets = FakeCollection()
    threads = FakeCollection()
    collections = {Collections.BULLETS: bullets, Collections.AGENT_THREADS: threads}
    finalize_module = import_module("app.agents.resume.nodes.finalize")
    monkeypatch.setattr(finalize_module, "collection", lambda name: collections[name])
    state = {
        "user_id": str(USER_ID),
        "thread_id": "gen_finalize_test",
        "profile_entry_id": str(ENTRY_ID),
        "completed_bullets": [
            {
                "achievement_id": "achievement-1",
                "draft": {
                    "action": "Reduced processing time",
                    "metric": "by 30% across daily runs",
                    "method": "by batching requests",
                    "skills": ["Python"],
                },
                "violations": [],
                "repair_attempts": 1,
            }
        ],
        "flagged_bullets": [],
    }

    result = asyncio.run(finalize(state))
    replay = asyncio.run(finalize(state))

    assert result["persisted_bullet_ids"] == [str(bullets.docs[0]["_id"])]
    assert replay["persisted_bullet_ids"] == result["persisted_bullet_ids"]
    assert len(bullets.docs) == 1
    assert bullets.docs[0]["user_id"] == USER_ID
    assert bullets.docs[0]["profile_entry_id"] == ENTRY_ID
    assert bullets.docs[0]["status"] == "approved"
