"""Database layer: Motor client, collection registry, index plan."""

from app.db.mongo import (
    Collections,
    collection,
    connect,
    disconnect,
    ensure_indexes,
    get_client,
    get_database,
)

__all__ = [
    "Collections",
    "collection",
    "connect",
    "disconnect",
    "ensure_indexes",
    "get_client",
    "get_database",
]
