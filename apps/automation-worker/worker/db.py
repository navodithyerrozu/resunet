"""Mongo access for the automation worker."""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from worker.settings import get_settings

_client: AsyncIOMotorClient[dict[str, Any]] | None = None


def connect() -> AsyncIOMotorDatabase[dict[str, Any]]:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncIOMotorClient(
            settings.mongodb_uri,
            maxPoolSize=10,
            minPoolSize=1,
            tz_aware=True,
            serverSelectionTimeoutMS=5000,
        )
    return _client[get_settings().mongodb_db]


def db() -> AsyncIOMotorDatabase[dict[str, Any]]:
    if _client is None:
        raise RuntimeError("Mongo not connected -- connect() runs in ARQ on_startup")
    return _client[get_settings().mongodb_db]


def client() -> AsyncIOMotorClient[dict[str, Any]]:
    """The raw client, needed to start a session for a transaction."""
    if _client is None:
        raise RuntimeError("Mongo not connected -- connect() runs in ARQ on_startup")
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
