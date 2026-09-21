"""Motor client lifecycle and typed collection accessors.

The client is a module-level singleton created during FastAPI's lifespan.
Motor multiplexes over an internal connection pool, so one client per
process is correct -- creating a client per request exhausts file
descriptors under load.

Collection names are centralised in `Collections` so a typo becomes an
AttributeError at import time rather than a silently-empty query against a
collection Mongo helpfully auto-creates.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from motor.motor_asyncio import (
    AsyncIOMotorClient,
    AsyncIOMotorCollection,
    AsyncIOMotorDatabase,
)
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class Collections(StrEnum):
    """Every collection name in one place."""

    USERS = "users"
    CAREER_PROFILES = "career_profiles"
    BULLETS = "bullets"
    RESUMES = "resumes"
    RESUME_VERSIONS = "resume_versions"
    RENDER_JOBS = "render_jobs"
    JOBS = "jobs"
    JOB_MATCHES = "job_matches"
    APPLICATIONS = "applications"
    BOARD_CREDENTIALS = "board_credentials"
    ATS_SCORES = "ats_scores"
    AGENT_THREADS = "agent_threads"
    # Owned by langgraph-checkpoint-mongodb, named here so index tooling and
    # backup scripts know they exist.
    AGENT_CHECKPOINTS = "agent_checkpoints"
    AGENT_CHECKPOINT_WRITES = "agent_checkpoint_writes"


_client: AsyncIOMotorClient[dict[str, Any]] | None = None


async def connect() -> AsyncIOMotorClient[dict[str, Any]]:
    """Open the shared client. Idempotent."""
    global _client
    if _client is not None:
        return _client

    settings = get_settings()
    _client = AsyncIOMotorClient(
        settings.mongodb_uri,
        # Aware datetimes everywhere; see app.models.base.utcnow for why.
        tz_aware=True,
        uuidRepresentation="standard",
        maxPoolSize=settings.mongo_max_pool_size,
        minPoolSize=settings.mongo_min_pool_size,
        # Fail fast rather than hanging a request for 30s on a dead primary.
        serverSelectionTimeoutMS=5_000,
        connectTimeoutMS=5_000,
        retryWrites=True,
    )
    await _client.admin.command("ping")
    logger.info("mongo.connected", database=settings.mongodb_db)
    return _client


async def disconnect() -> None:
    """Close the shared client on shutdown."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
        logger.info("mongo.disconnected")


def get_client() -> AsyncIOMotorClient[dict[str, Any]]:
    if _client is None:
        raise RuntimeError("Mongo client not initialised -- connect() runs in app lifespan")
    return _client


def get_database() -> AsyncIOMotorDatabase[dict[str, Any]]:
    return get_client()[get_settings().mongodb_db]


def collection(name: Collections) -> AsyncIOMotorCollection[dict[str, Any]]:
    """Fetch a collection by enum member."""
    return get_database()[name.value]


# ---------------------------------------------------------------------------
# Index plan
#
# Every index below earns its place against a specific query in the app.
# Notes on the non-obvious ones:
#
# * Compound index field order follows ESR (Equality, Sort, Range). Nearly
#   every query is scoped by `user_id` equality and sorted by recency, so
#   `(user_id, updated_at DESC)` is the recurring shape.
#
# * Uniqueness is enforced in the database, not only in application code.
#   `(user_id, job_id)` unique on applications is what actually stops a
#   duplicate application when two requests race -- an application-level
#   "check then insert" cannot.
#
# * Partial indexes are used where a field is usually null (e.g. only some
#   applications carry an idempotency key), which keeps the index small and
#   lets the unique constraint coexist with many nulls.
#
# * TTL on render_jobs reaps completed compile records automatically; they
#   are operational telemetry, not user data.
# ---------------------------------------------------------------------------

INDEX_PLAN: dict[Collections, list[IndexModel]] = {
    Collections.USERS: [
        # Login path. Unique because email is the identity key.
        IndexModel([("email", ASCENDING)], unique=True, name="uq_email"),
        IndexModel([("status", ASCENDING)], name="ix_status"),
    ],
    Collections.CAREER_PROFILES: [
        # Exactly one profile per user.
        IndexModel([("user_id", ASCENDING)], unique=True, name="uq_user"),
    ],
    Collections.BULLETS: [
        # Editor loads all bullets for one experience entry.
        IndexModel(
            [("user_id", ASCENDING), ("profile_entry_id", ASCENDING), ("created_at", DESCENDING)],
            name="ix_user_entry_created",
        ),
        # "Bullets needing review" queue. Partial: only a small tail matches,
        # so indexing the rest would be dead weight.
        IndexModel(
            [("user_id", ASCENDING), ("status", ASCENDING)],
            name="ix_user_needs_review",
            partialFilterExpression={"status": "needs_review"},
        ),
        # Keyword-driven bullet suggestion when tailoring to a posting.
        IndexModel([("user_id", ASCENDING), ("skills", ASCENDING)], name="ix_user_skills"),
        # Full-text fallback for the editor's search box. One text index per
        # collection is a hard Mongo limit, so it covers all three components.
        IndexModel(
            [("action", "text"), ("metric", "text"), ("method", "text")],
            name="tx_bullet_content",
            weights={"action": 3, "metric": 2, "method": 1},
        ),
    ],
    Collections.RESUMES: [
        IndexModel(
            [("user_id", ASCENDING), ("updated_at", DESCENDING)],
            name="ix_user_updated",
        ),
        IndexModel(
            [("user_id", ASCENDING), ("status", ASCENDING)], name="ix_user_status"
        ),
        # Resuming an in-flight agent conversation by thread.
        IndexModel(
            [("thread_id", ASCENDING)],
            name="ix_thread",
            partialFilterExpression={"thread_id": {"$type": "string"}},
        ),
    ],
    Collections.RESUME_VERSIONS: [
        # Version history for one resume, newest first.
        IndexModel(
            [("resume_id", ASCENDING), ("version", DESCENDING)],
            unique=True,
            name="uq_resume_version",
        ),
        IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="ix_user_created"),
        # Skip recompiling a byte-identical document.
        IndexModel(
            [("pdf_sha256", ASCENDING)],
            name="ix_pdf_hash",
            partialFilterExpression={"pdf_sha256": {"$type": "string"}},
        ),
    ],
    Collections.RENDER_JOBS: [
        IndexModel([("user_id", ASCENDING), ("created_at", DESCENDING)], name="ix_user_created"),
        IndexModel([("version_id", ASCENDING)], name="ix_version"),
        IndexModel([("status", ASCENDING), ("created_at", ASCENDING)], name="ix_status_created"),
        # Reap finished compile records after 14 days.
        IndexModel(
            [("finished_at", ASCENDING)],
            name="ttl_finished",
            expireAfterSeconds=14 * 24 * 3600,
        ),
    ],
    Collections.JOBS: [
        # Global dedupe key. Partial so postings pasted by hand (no external
        # id) do not all collide on null.
        IndexModel(
            [("source.board", ASCENDING), ("source.external_id", ASCENDING)],
            unique=True,
            name="uq_board_external_id",
            partialFilterExpression={"source.external_id": {"$type": "string"}},
        ),
        IndexModel([("status", ASCENDING), ("posted_at", DESCENDING)], name="ix_status_posted"),
        IndexModel([("company_name", ASCENDING)], name="ix_company"),
        # Job search box.
        IndexModel(
            [("title", "text"), ("company_name", "text"), ("description_raw", "text")],
            name="tx_job_content",
            weights={"title": 5, "company_name": 3, "description_raw": 1},
        ),
    ],
    Collections.JOB_MATCHES: [
        IndexModel(
            [("user_id", ASCENDING), ("job_id", ASCENDING)], unique=True, name="uq_user_job"
        ),
        # "Best matches for me" -- the dashboard's primary query.
        IndexModel([("user_id", ASCENDING), ("score", DESCENDING)], name="ix_user_score"),
    ],
    Collections.APPLICATIONS: [
        # Hard stop on applying twice to the same posting.
        IndexModel(
            [("user_id", ASCENDING), ("job_id", ASCENDING)], unique=True, name="uq_user_job"
        ),
        # Tracker board, grouped by column and sorted by recency.
        IndexModel(
            [("user_id", ASCENDING), ("status", ASCENDING), ("updated_at", DESCENDING)],
            name="ix_user_status_updated",
        ),
        # Idempotent submit. Partial because most rows have no key.
        IndexModel(
            [("idempotency_key", ASCENDING)],
            unique=True,
            name="uq_idempotency_key",
            partialFilterExpression={"idempotency_key": {"$type": "string"}},
        ),
        # Follow-up reminder sweep.
        IndexModel(
            [("follow_up_at", ASCENDING)],
            name="ix_follow_up",
            partialFilterExpression={"follow_up_at": {"$type": "date"}},
        ),
        IndexModel([("resume_version_id", ASCENDING)], name="ix_resume_version"),
    ],
    Collections.BOARD_CREDENTIALS: [
        IndexModel(
            [("user_id", ASCENDING), ("board", ASCENDING)], unique=True, name="uq_user_board"
        ),
    ],
    Collections.ATS_SCORES: [
        # Score history for one version (append-only, newest first).
        IndexModel(
            [("resume_version_id", ASCENDING), ("computed_at", DESCENDING)],
            name="ix_version_computed",
        ),
        IndexModel([("user_id", ASCENDING), ("computed_at", DESCENDING)], name="ix_user_computed"),
        # Latest score for a (version, job) pair -- the compare view.
        IndexModel(
            [("resume_version_id", ASCENDING), ("job_id", ASCENDING), ("computed_at", DESCENDING)],
            name="ix_version_job_computed",
        ),
    ],
    Collections.AGENT_THREADS: [
        IndexModel([("thread_id", ASCENDING)], unique=True, name="uq_thread"),
        IndexModel(
            [("user_id", ASCENDING), ("status", ASCENDING), ("last_message_at", DESCENDING)],
            name="ix_user_status_last_message",
        ),
        IndexModel([("resume_id", ASCENDING)], name="ix_resume"),
    ],
}


async def ensure_indexes() -> dict[str, list[str]]:
    """Create every index in the plan. Idempotent and safe to run at boot.

    `create_indexes` is a no-op for indexes that already exist with identical
    options. Changing options on an existing index name raises instead of
    silently doing nothing -- rename the index when you change its shape.
    """
    db = get_database()
    created: dict[str, list[str]] = {}
    for coll_name, models in INDEX_PLAN.items():
        if not models:
            continue
        names = await db[coll_name.value].create_indexes(models)
        created[coll_name.value] = names
        logger.info("mongo.indexes_ensured", collection=coll_name.value, count=len(names))
    return created
