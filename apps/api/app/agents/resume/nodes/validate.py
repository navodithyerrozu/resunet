"""Node: validate the current draft, and the routing decision that follows.

This node contains no LLM call. It delegates to
`BulletDraft.validate_xyz()`, which is pure Python.

That is deliberate and it is the crux of the design. An LLM-as-judge here
would be slower, non-deterministic, and would occasionally pass a bullet with
no metric -- exactly the failure the product exists to prevent. A regex-and-
rules validator is instant, free, identical on every run, and unit-testable.
The LLM's job is to *fix* what this function reports, never to decide whether
there is a problem.
"""

from __future__ import annotations

from typing import Literal

from app.agents.resume.nodes.draft import MAX_BULLET_CHARS, current_achievement
from app.agents.resume.state import CompletedBullet, ResumeAgentState
from app.core.config import get_settings
from app.core.logging import get_logger
from app.models.bullet import XYZViolationCode

logger = get_logger(__name__)


async def validate_bullet(state: ResumeAgentState) -> dict[str, object]:
    """Run deterministic XYZ validation on the current draft."""
    draft = state.get("current_draft")
    if draft is None:
        return {"status": "drafting"}

    violations = draft.validate_xyz(max_chars=MAX_BULLET_CHARS)

    logger.info(
        "agent.validated_bullet",
        thread_id=state.get("thread_id"),
        compliant=not violations,
        codes=[v.code.value for v in violations],
        attempts=state.get("repair_attempts", 0),
    )

    return {
        "current_violations": violations,
        "status": "validating",
    }


def route_after_validation(
    state: ResumeAgentState,
) -> Literal["accept", "repair", "clarify", "flag"]:
    """Conditional edge: decide what happens to a validated draft.

    Four outcomes, in priority order:

    * **accept** -- no violations. Persist and move on.

    * **clarify** -- the only remaining problem is a missing or vague metric.
      Interrupt and ask the user. Routing here *before* spending repair
      attempts is the important choice: no amount of re-prompting can conjure
      a number that was never in the notes, so retrying first just burns
      tokens and latency to arrive at the same question. It also removes the
      model's incentive to fabricate, since asking is an available action.

    * **repair** -- fixable defects (weak verb, first person, too long) with
      budget left. These are genuine rewriting problems the model can solve.

    * **flag** -- budget exhausted. Save as NEEDS_REVIEW rather than silently
      dropping the bullet or persisting a non-compliant one.
    """
    violations = state.get("current_violations", [])
    if not violations:
        return "accept"

    codes = {v.code for v in violations}
    metric_codes = {XYZViolationCode.MISSING_METRIC, XYZViolationCode.VAGUE_METRIC}

    # Metric gaps are a data problem, not a wording problem -- ask, don't retry.
    if codes & metric_codes and not state.get("user_response"):
        return "clarify"

    if state.get("repair_attempts", 0) < get_settings().xyz_max_repair_attempts:
        return "repair"

    return "flag"


async def accept_bullet(state: ResumeAgentState) -> dict[str, object]:
    """Record a compliant bullet and advance the queue."""
    draft = state.get("current_draft")
    achievement = current_achievement(state)
    if draft is None or achievement is None:
        return {"status": "done"}

    completed = CompletedBullet(
        achievement_id=str(achievement["id"]),
        draft=draft.model_dump(),
        text=draft.text,
        repair_attempts=state.get("repair_attempts", 0),
        accepted=True,
        violations=[],
    )
    return {
        "completed_bullets": [completed],
        "current_achievement_index": state.get("current_achievement_index", 0) + 1,
        "current_draft": None,
        "current_violations": [],
        "repair_attempts": 0,
        "status": "drafting",
        "messages": [{"role": "assistant", "content": f"Bullet ready: {draft.text}"}],
    }


async def flag_bullet(state: ResumeAgentState) -> dict[str, object]:
    """Set aside a bullet that could not be made compliant.

    Kept rather than discarded, with its violations attached, so the editor can
    show it as needing attention. Discarding it would leave the user wondering
    where their achievement went; persisting it as approved would put a
    metric-free bullet in a PDF.
    """
    draft = state.get("current_draft")
    achievement = current_achievement(state)
    if draft is None or achievement is None:
        return {"status": "done"}

    violations = state.get("current_violations", [])
    flagged = CompletedBullet(
        achievement_id=str(achievement["id"]),
        draft=draft.model_dump(),
        text=draft.text,
        repair_attempts=state.get("repair_attempts", 0),
        accepted=False,
        violations=[v.model_dump() for v in violations],
    )

    logger.warning(
        "agent.bullet_flagged",
        thread_id=state.get("thread_id"),
        achievement_id=achievement["id"],
        codes=[v.code.value for v in violations],
    )

    reason = violations[0].message if violations else "did not meet the XYZ standard"
    return {
        "flagged_bullets": [flagged],
        "current_achievement_index": state.get("current_achievement_index", 0) + 1,
        "current_draft": None,
        "current_violations": [],
        "repair_attempts": 0,
        "status": "drafting",
        "messages": [
            {
                "role": "assistant",
                "content": f"I've saved this one for review -- {reason}",
            }
        ],
    }


def route_next_achievement(state: ResumeAgentState) -> Literal["draft", "finalize"]:
    """Conditional edge: another achievement to process, or wrap up?"""
    idx = state.get("current_achievement_index", 0)
    return "draft" if idx < len(state.get("achievements", [])) else "finalize"
