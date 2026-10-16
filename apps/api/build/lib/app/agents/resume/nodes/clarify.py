"""Node: interrupt the graph to ask the user for a missing metric.

Uses LangGraph's `interrupt()`, which suspends execution and persists state to
the checkpointer. The API returns the question; when the user answers, the
graph resumes from exactly this point with their reply.

Why an interrupt rather than a tool call or a loop: the wait is unbounded. The
user might answer in ten seconds or tomorrow morning. Holding a request open
or keeping state in memory cannot survive that, but a checkpointed interrupt
can -- the process can restart and the conversation still resumes correctly.
"""

from __future__ import annotations

from langgraph.types import interrupt

from app.agents.resume.llm import get_chat_model
from app.agents.resume.nodes.draft import current_achievement
from app.agents.resume.prompts import CLARIFY_SYSTEM, CLARIFY_USER
from app.agents.resume.state import ResumeAgentState
from app.core.logging import get_logger

logger = get_logger(__name__)


async def clarify_metric(state: ResumeAgentState) -> dict[str, object]:
    """Ask the user for the number the notes are missing, then wait."""
    achievement = current_achievement(state)
    if achievement is None:
        return {"status": "done"}

    draft = state.get("current_draft")
    prior_attempt = (
        f"A first pass produced: \"{draft.text}\"\nIts Y component has no real "
        "measurement."
        if draft
        else ""
    )

    # Slightly higher temperature: question phrasing benefits from variety,
    # and repeating a question the user has already struggled with verbatim is
    # a poor experience.
    model = get_chat_model(temperature=0.5)
    response = await model.ainvoke(
        [
            {"role": "system", "content": CLARIFY_SYSTEM},
            {
                "role": "user",
                "content": CLARIFY_USER.format(
                    achievement_text=achievement["raw_text"],
                    prior_attempt=prior_attempt,
                ),
            },
        ]
    )
    question = (
        response.content if isinstance(response.content, str) else str(response.content)
    ).strip()

    logger.info(
        "agent.clarification_requested",
        thread_id=state.get("thread_id"),
        achievement_id=achievement["id"],
    )

    # Execution halts here. State is checkpointed; the coroutine does not
    # resume until the API calls the graph again with Command(resume=...).
    user_answer: str = interrupt(
        {
            "question": question,
            "achievement": achievement["raw_text"],
            "reason": "missing_metric",
        }
    )

    return {
        "user_response": user_answer,
        "pending_question": None,
        "status": "drafting",
        "messages": [
            {"role": "assistant", "content": question},
            {"role": "user", "content": user_answer},
        ],
    }
