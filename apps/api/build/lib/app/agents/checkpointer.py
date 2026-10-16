"""LangGraph checkpoint storage backed by MongoDB.

The MongoDB saver owns its checkpoint collections. Application code uses
`AgentThread` for listable conversation metadata and never reads saver state
directly. Current ``langgraph-checkpoint-mongodb`` releases expose async saver
methods that delegate synchronous PyMongo work to an executor, so this module
owns one small PyMongo client for checkpointing alongside the API's Motor
client.
"""

from __future__ import annotations

import asyncio
from threading import Lock
from typing import Any

from langgraph.checkpoint.mongodb import MongoDBSaver
from pymongo import MongoClient

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.mongo import Collections

logger = get_logger(__name__)

_checkpointer: MongoDBSaver | None = None
_client: MongoClient[dict[str, Any]] | None = None
_creation_lock = Lock()


def _create_checkpointer() -> tuple[MongoClient[dict[str, Any]], MongoDBSaver]:
    """Create the sync Mongo client and saver outside the event loop."""
    settings = get_settings()
    client: MongoClient[dict[str, Any]] = MongoClient(
        settings.mongodb_uri,
        tz_aware=True,
        uuidRepresentation="standard",
        maxPoolSize=settings.mongo_max_pool_size,
        minPoolSize=settings.mongo_min_pool_size,
        serverSelectionTimeoutMS=5_000,
        connectTimeoutMS=5_000,
        retryWrites=True,
    )
    client.admin.command("ping")
    saver = MongoDBSaver(
        client=client,
        db_name=settings.mongodb_db,
        checkpoint_collection_name=Collections.AGENT_CHECKPOINTS.value,
        writes_collection_name=Collections.AGENT_CHECKPOINT_WRITES.value,
    )
    return client, saver


async def get_checkpointer() -> MongoDBSaver:
    """Return the process-wide Mongo saver, initializing it once."""
    global _checkpointer, _client
    if _checkpointer is not None:
        return _checkpointer

    def create_once() -> tuple[MongoClient[dict[str, Any]], MongoDBSaver]:
        global _checkpointer, _client
        with _creation_lock:
            if _checkpointer is None:
                _client, _checkpointer = _create_checkpointer()
            if _client is None or _checkpointer is None:
                raise RuntimeError("Mongo checkpointer initialization did not complete")
            return _client, _checkpointer

    _, saver = await asyncio.to_thread(create_once)
    logger.info("checkpointer.initialised", database=get_settings().mongodb_db)
    return saver


def reset_checkpointer() -> None:
    """Close saver resources during app shutdown or test teardown."""
    global _checkpointer, _client
    if _client is not None:
        _client.close()
    _checkpointer = None
    _client = None


def thread_config(thread_id: str, **extra: Any) -> dict[str, Any]:
    """Build the configurable block identifying a persistent conversation."""
    return {"configurable": {"thread_id": thread_id, **extra}}
