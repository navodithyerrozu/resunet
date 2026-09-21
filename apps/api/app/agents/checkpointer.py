"""LangGraph checkpointer backed by MongoDB.

Why Mongo and not memory: resume generation is a multi-turn conversation that
interrupts to ask the user for a metric. That wait is unbounded -- the user may
answer in ten seconds or reopen the tab tomorrow. An in-memory saver loses the
thread on any process restart or, in a multi-replica deployment, whenever the
next request lands on a different pod. Persisting checkpoints makes the
conversation survive both.

The checkpointer owns `agent_checkpoints` and `agent_checkpoint_writes`. Their
schema belongs to LangGraph -- application code must not read them directly.
`app.models.agent.AgentThread` exists precisely so product features have their
own queryable view of a conversation.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.mongodb.aio import AsyncMongoDBSaver

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.mongo import Collections, get_client

logger = get_logger(__name__)

_checkpointer: AsyncMongoDBSaver | None = None


async def get_checkpointer() -> AsyncMongoDBSaver:
    """Return the shared checkpointer, creating it on first use.

    Reuses the application's Motor client rather than opening its own
    connection pool -- two pools against the same cluster from one process is
    wasted file descriptors and doubles the connection count Mongo sees.
    """
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    settings = get_settings()
    _checkpointer = AsyncMongoDBSaver(
        client=get_client(),
        db_name=settings.mongodb_db,
        checkpoint_collection_name=Collections.AGENT_CHECKPOINTS.value,
        writes_collection_name=Collections.AGENT_CHECKPOINT_WRITES.value,
    )
    logger.info("checkpointer.initialised", database=settings.mongodb_db)
    return _checkpointer


def reset_checkpointer() -> None:
    """Drop the cached instance. For test teardown between event loops."""
    global _checkpointer
    _checkpointer = None


def thread_config(thread_id: str, **extra: Any) -> dict[str, Any]:
    """Build the `configurable` block that identifies a conversation.

    Every graph invocation needs this. Omitting `thread_id` makes LangGraph
    treat the run as brand new, silently discarding prior state -- which
    presents as "the agent forgot everything" rather than as an error.
    """
    return {"configurable": {"thread_id": thread_id, **extra}}
