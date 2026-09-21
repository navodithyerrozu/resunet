"""Mongo access for the render worker.

Separate client from the API's. Workers and the API scale independently and a
worker holding connections open against the API's pool budget is how you
exhaust `maxIncomingConnections` during a traffic spike.
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from worker.settings import get_settings

_client: AsyncIOMotorClient | None = None


def connect() -> AsyncIOMotorDatabase:
    """Open the connection pool. Called from ARQ's `on_startup`."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncIOMotorClient(
            settings.mongodb_uri,
            # Small pool: concurrency here is bounded by CPU-bound pdflatex,
            # not by database round-trips.
            maxPoolSize=10,
            minPoolSize=1,
            tz_aware=True,
            serverSelectionTimeoutMS=5000,
        )
    return _client[get_settings().mongodb_db]


def db() -> AsyncIOMotorDatabase:
    if _client is None:
        raise RuntimeError("Mongo not connected -- connect() runs in ARQ on_startup")
    return _client[get_settings().mongodb_db]


async def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
