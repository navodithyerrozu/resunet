"""Node: draft one bullet from the current achievement.

Produces X, Y, Z as separate fields via structured output. It never assembles
the sentence -- `BulletDraft.text` does that in Python, which is why the
model cannot drift the format.
"""

from __future__ import annotations

from app.agents.resume.llm import structured
from app.agents.resume.prompts import (
    DRAFTING_SYSTEM,
    DRAFTING_USER,
    format_keyword_guidance,
)
from app.agents.resume.state import ResumeAgentState
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.bullet import BulletDraft

logger = get_logger(__name__)

MAX_BULLET_CHARS = 240


def current_achievement(state: ResumeAgentState) -> dict[str, object] | None:
    """The achievement being worked on, or None when the queue is exhausted."""
    achievements = state.get("achievements", [])
    idx = state.get("current_achievement_index", 0)
    return dict(achievements[idx]) if 0 <= idx < len(achievements) else None


async def draft_bullet(state: ResumeAgentState) -> dict[str, object]:
    """Generate the initial XYZ draft for the current achievement."""
    achievement = current_achievement(state)
    if achievement is None:
        return {"status": "done"}

    achievement_text = str(achievement["raw_text"])

    # A user answer supplied by the clarify node is appended to the achievement
    # text rather than kept in a separate field, so the drafting prompt sees
    # one coherent description instead of having to reconcile two sources.
    if response := state.get("user_response"):
        achievement_text = f"{achievement_text}\n\nCandidate added: {response}"

    model = structured(BulletDraft)
    draft: BulletDraft = await model.ainvoke(
        [
            {"role": "system", "content": DRAFTING_SYSTEM.format(max_chars=MAX_BULLET_CHARS)},
            {
                "role": "user",
                "content": DRAFTING_USER.format(
                    achievement_text=achievement_text,
                    keyword_guidance=format_keyword_guidance(state.get("target_keywords", [])),
                ),
            },
        ]
    )

    logger.info(
        "agent.drafted_bullet",
        thread_id=state.get("thread_id"),
        achievement_id=achievement["id"],
        chars=len(draft.text),
    )

    return {
        "current_draft": draft,
        "repair_attempts": 0,
        "current_violations": [],
        # Consumed, so clear it -- leaving it set would leak this answer into
        # the next achievement's draft.
        "user_response": None,
        "status": "validating",
    }


async def repair_bullet(state: ResumeAgentState) -> dict[str, object]:
    """Revise a draft that failed validation.

    Separate from `draft_bullet` because the prompt is different in kind: it
    receives the failing draft plus the exact violations and is told to fix
    only those. Re-running the drafting prompt instead tends to produce a
    different bullet with a different set of defects, which does not converge.
    """
    from app.agents.resume.prompts import REPAIR_SYSTEM, REPAIR_USER, format_violations

    draft = state.get("current_draft")
    if draft is None:
        return {"status": "drafting"}

    attempts = state.get("repair_attempts", 0) + 1
    max_attempts = get_settings().xyz_max_repair_attempts
    violations = [v.model_dump() for v in state.get("current_violations", [])]

    model = structured(BulletDraft)
    repaired: BulletDraft = await model.ainvoke(
        [
            {"role": "system", "content": REPAIR_SYSTEM},
            {
                "role": "user",
                "content": REPAIR_USER.format(
                    action=draft.action,
                    metric=draft.metric,
                    method=draft.method,
                    violations=format_violations(violations),
                    attempt=attempts,
                    max_attempts=max_attempts,
                ),
            },
        ]
    )

    logger.info(
        "agent.repaired_bullet",
        thread_id=state.get("thread_id"),
        attempt=attempts,
        violation_codes=[v.get("code") for v in violations],
    )

    return {
        "current_draft": repaired,
        "repair_attempts": attempts,
        "status": "validating",
    }
