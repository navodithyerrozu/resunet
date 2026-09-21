"""Graph state for the resume-generation agent.

State design notes
------------------
LangGraph merges node return values into this TypedDict. Two conventions
matter:

* Fields that *accumulate* use `Annotated[list, add]` reducers, so a node can
  return only its additions rather than the whole list. Nodes that return a
  full list for an accumulating field will duplicate entries.

* Fields that *replace* are plain types. `current_draft` is replaced on every
  repair pass, which is what we want -- we do not want a history of failed
  drafts in state, only the live one.

State is checkpointed to Mongo after every node, so it must stay
JSON-serializable. No ObjectId, no datetime -- ids are strings and timestamps
are ISO strings. This is the constraint people hit last and hardest.
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Literal, TypedDict

from app.models.bullet import BulletDraft, XYZViolation


class CandidateAchievement(TypedDict):
    """One achievement mined from raw notes, before it becomes a bullet."""

    id: str
    raw_text: str
    has_metric: bool
    needs_clarification: bool


class CompletedBullet(TypedDict):
    """A finalized bullet plus the audit trail of how it got there."""

    achievement_id: str
    draft: dict[str, object]
    text: str
    repair_attempts: int
    accepted: bool
    violations: list[dict[str, object]]


class ResumeAgentState(TypedDict, total=False):
    """The graph's working memory.

    `total=False` because nodes populate fields incrementally; requiring every
    key at every step would mean each node returning fields it does not own.
    """

    # --- Immutable run context (set once at entry) ------------------------
    user_id: str
    thread_id: str
    resume_id: str | None
    profile_entry_id: str | None
    target_job_id: str | None

    # Source material the agent mines. Free-form and messy by design.
    raw_notes: str
    # Keywords from the target posting that the resume is missing. Steers
    # generation toward terms the ATS is scanning for -- without inventing
    # experience the user never claimed.
    target_keywords: list[str]
    entry_context: dict[str, object]

    # --- Conversation ----------------------------------------------------
    # Accumulates. `add` appends rather than replacing.
    messages: Annotated[list[dict[str, str]], add]
    # Set when the graph interrupts for input; cleared once answered.
    pending_question: str | None
    user_response: str | None

    # --- Work queue ------------------------------------------------------
    achievements: list[CandidateAchievement]
    current_achievement_index: int

    # --- The draft under construction ------------------------------------
    # Replaced (not accumulated) on each repair pass.
    current_draft: BulletDraft | None
    current_violations: list[XYZViolation]
    repair_attempts: int

    # --- Output ----------------------------------------------------------
    completed_bullets: Annotated[list[CompletedBullet], add]
    flagged_bullets: Annotated[list[CompletedBullet], add]

    # --- Control ---------------------------------------------------------
    status: Literal["mining", "drafting", "validating", "repairing", "awaiting_user", "done"]
    error: str | None
    total_tokens: int


def initial_state(
    *,
    user_id: str,
    thread_id: str,
    raw_notes: str,
    resume_id: str | None = None,
    profile_entry_id: str | None = None,
    target_job_id: str | None = None,
    target_keywords: list[str] | None = None,
) -> ResumeAgentState:
    """Build the entry state for a fresh run.

    Every accumulating field is explicitly initialised to an empty list.
    Leaving them unset makes the first reducer call operate on `None`, which
    fails inside LangGraph rather than in your code, where the traceback is
    considerably less helpful.
    """
    return ResumeAgentState(
        user_id=user_id,
        thread_id=thread_id,
        resume_id=resume_id,
        profile_entry_id=profile_entry_id,
        target_job_id=target_job_id,
        raw_notes=raw_notes,
        target_keywords=target_keywords or [],
        entry_context={},
        messages=[],
        pending_question=None,
        user_response=None,
        achievements=[],
        current_achievement_index=0,
        current_draft=None,
        current_violations=[],
        repair_attempts=0,
        completed_bullets=[],
        flagged_bullets=[],
        status="mining",
        error=None,
        total_tokens=0,
    )
