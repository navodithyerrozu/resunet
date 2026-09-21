"""Node: mine raw notes into discrete candidate achievements.

First node in the graph. Turns a paragraph of informal recollection into a
work queue of individual achievements, each tagged with whether it already
carries a metric.

That tag drives routing: achievements with a metric go straight to drafting;
those without route to the clarify node, which interrupts and asks the user.
Doing this classification up front means we ask all our questions early rather
than discovering the gap three nodes deep.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.agents.resume.llm import structured
from app.agents.resume.prompts import (
    MINING_SYSTEM,
    MINING_USER,
    format_entry_context,
)
from app.agents.resume.state import CandidateAchievement, ResumeAgentState
from app.core.logging import get_logger

logger = get_logger(__name__)


class _MinedAchievement(BaseModel):
    """One achievement as extracted by the model."""

    raw_text: str = Field(description="The achievement, quoted or lightly paraphrased.")
    has_metric: bool = Field(
        description="True only if the notes contain an actual number, percentage, "
        "scale, or duration. Vague intensifiers do not count."
    )
    metric_hint: str | None = Field(
        default=None,
        description="If a metric is present, the specific figure found.",
    )


class _MiningResult(BaseModel):
    achievements: list[_MinedAchievement] = Field(
        default_factory=list, description="Distinct achievements, most significant first."
    )


async def mine_achievements(state: ResumeAgentState) -> dict[str, object]:
    """Extract candidate achievements from raw notes."""
    raw_notes = state.get("raw_notes", "").strip()

    if not raw_notes:
        # No source material. Ending here with a question is far better than
        # inviting the model to generate a resume from nothing.
        return {
            "achievements": [],
            "status": "awaiting_user",
            "pending_question": (
                "Tell me about this role -- what did you work on, and what changed "
                "because of it? Rough notes are fine; I'll do the shaping."
            ),
            "messages": [
                {"role": "assistant", "content": "I need some notes about this role to start."}
            ],
        }

    model = structured(_MiningResult)
    result: _MiningResult = await model.ainvoke(
        [
            {"role": "system", "content": MINING_SYSTEM},
            {
                "role": "user",
                "content": MINING_USER.format(
                    raw_notes=raw_notes,
                    entry_context=format_entry_context(state.get("entry_context", {})),
                ),
            },
        ]
    )

    achievements: list[CandidateAchievement] = [
        CandidateAchievement(
            id=f"ach_{i}",
            raw_text=item.raw_text,
            has_metric=item.has_metric,
            needs_clarification=not item.has_metric,
        )
        for i, item in enumerate(result.achievements)
    ]

    quantified = sum(1 for a in achievements if a["has_metric"])
    logger.info(
        "agent.mined_achievements",
        thread_id=state.get("thread_id"),
        total=len(achievements),
        quantified=quantified,
    )

    if not achievements:
        return {
            "achievements": [],
            "status": "awaiting_user",
            "pending_question": (
                "I couldn't find a concrete accomplishment in those notes. What's one "
                "thing that was different at the company because you were there?"
            ),
            "messages": [
                {
                    "role": "assistant",
                    "content": "Those notes read more like responsibilities than outcomes.",
                }
            ],
        }

    summary = (
        f"Found {len(achievements)} achievement(s). "
        f"{quantified} already have numbers; "
        f"{len(achievements) - quantified} need a metric before they're usable."
    )
    return {
        "achievements": achievements,
        "current_achievement_index": 0,
        "status": "drafting",
        "messages": [{"role": "assistant", "content": summary}],
    }
