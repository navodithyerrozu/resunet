"""Node: persist results and close out the run.

Terminal node. Writes bullets to Mongo and updates the thread record.

Persistence happens here rather than incrementally per bullet for one reason:
if the user abandons the conversation halfway, a partial set of bullets
attached to their profile is confusing -- they see three bullets from a
conversation they remember not finishing. The graph's checkpointed state is
already the durable record of progress, so nothing is lost by deferring the
write, and an abandoned run leaves the profile untouched.
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId

from app.agents.resume.state import ResumeAgentState
from app.core.logging import get_logger
from app.db.mongo import Collections, collection
from app.models.base import utcnow
from app.models.bullet import (
    Bullet,
    BulletSource,
    BulletStatus,
    XYZViolation,
)

logger = get_logger(__name__)


def _to_bullet(
    *,
    user_id: str,
    profile_entry_id: str,
    thread_id: str,
    payload: dict[str, Any],
    accepted: bool,
) -> Bullet:
    """Build a persistable Bullet from a completed graph result."""
    achievement_id = payload.get("achievement_id")
    if not isinstance(achievement_id, str) or not achievement_id:
        raise ValueError("completed generation bullet is missing its achievement id")
    draft = payload["draft"]
    violations = [XYZViolation.model_validate(v) for v in payload.get("violations", [])]
    return Bullet(
        user_id=user_id,
        profile_entry_id=profile_entry_id,
        action=str(draft["action"]),
        metric=str(draft["metric"]),
        method=str(draft["method"]),
        skills=list(draft.get("skills", [])),
        status=BulletStatus.APPROVED if accepted else BulletStatus.NEEDS_REVIEW,
        source=BulletSource.AGENT_GENERATED,
        violations=violations,
        thread_id=thread_id,
        generation_achievement_id=achievement_id,
        repair_attempts=int(payload.get("repair_attempts", 0)),
    )


async def finalize(state: ResumeAgentState) -> dict[str, object]:
    """Persist bullets and mark the thread complete."""
    user_id = state.get("user_id", "")
    thread_id = state.get("thread_id", "")
    profile_entry_id = state.get("profile_entry_id") or str(ObjectId())

    completed = state.get("completed_bullets", [])
    flagged = state.get("flagged_bullets", [])

    bullets = [
        _to_bullet(
            user_id=user_id,
            profile_entry_id=profile_entry_id,
            thread_id=thread_id,
            payload=dict(item),
            accepted=True,
        )
        for item in completed
    ] + [
        _to_bullet(
            user_id=user_id,
            profile_entry_id=profile_entry_id,
            thread_id=thread_id,
            payload=dict(item),
            accepted=False,
        )
        for item in flagged
    ]

    persisted_ids: list[str] = []
    if bullets:
        bullet_collection = collection(Collections.BULLETS)
        for bullet in bullets:
            bullet_id = ObjectId()
            document = bullet.to_mongo()
            document.update(
                {
                    "_id": bullet_id,
                    "user_id": ObjectId(user_id),
                    "profile_entry_id": ObjectId(profile_entry_id),
                }
            )
            identity = {
                "user_id": ObjectId(user_id),
                "thread_id": thread_id,
                "generation_achievement_id": bullet.generation_achievement_id,
            }
            await bullet_collection.update_one(identity, {"$setOnInsert": document}, upsert=True)
            saved = await bullet_collection.find_one(identity)
            if saved is None:
                raise RuntimeError("finalized bullet was not readable after its upsert")
            persisted_ids.append(str(saved["_id"]))

    await collection(Collections.AGENT_THREADS).update_one(
        {"thread_id": thread_id},
        {
            "$set": {
                "status": "completed",
                "bullets_produced": len(completed),
                "bullets_flagged": len(flagged),
                "last_message_at": utcnow(),
                "updated_at": utcnow(),
            }
        },
    )

    logger.info(
        "agent.finalized",
        thread_id=thread_id,
        accepted=len(completed),
        flagged=len(flagged),
    )

    if flagged:
        summary = (
            f"Done -- {len(completed)} bullet(s) ready and {len(flagged)} needing a "
            "number before they're usable. They're in the editor marked for review."
        )
    else:
        summary = f"Done -- {len(completed)} bullet(s), every one with a measurable outcome."

    return {
        "status": "done",
        "persisted_bullet_ids": persisted_ids,
        "messages": [{"role": "assistant", "content": summary}],
    }
