"""ARQ queue client.

The API never runs a LaTeX compile or drives a browser inline; it enqueues
and returns a job handle. Two separate queues rather than one because the
workloads need independent scaling and, more importantly, independent failure
domains: a wedged browser pool must not starve PDF rendering.

Job ids are deterministic where possible. ARQ deduplicates by `_job_id`, so
deriving the id from the resume version means a double-clicked "Render"
button enqueues one compile, not two.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class TaskName(StrEnum):
    """Task names shared with the workers.

    These strings are a contract across process boundaries: the worker registers
    functions under exactly these names, and a mismatch is invisible at run time
    -- ARQ accepts the enqueue, the job sits in Redis under a name nothing
    consumes, and it expires silently. Nobody gets an error; the user just never
    sees their PDF.

    `tests/test_queue_contract.py` at the repo root is what actually enforces
    this. The import-time guards in each worker's `main.py` cannot: a worker only
    sees its own registrations, so neither one can tell that a name in this enum
    has no consumer anywhere.
    """

    # Implemented and consumed by a worker.
    RENDER_RESUME = "render_resume_pdf"
    SCORE_RESUME = "score_resume_ats"
    SUBMIT_APPLICATION = "submit_application"

    # NOT YET IMPLEMENTED -- no worker registers these. Enqueueing one today
    # loses the job silently. They are listed here because the routes that will
    # need them already reference them in their docstrings, and a name declared
    # in one place is easier to keep honest than one invented at the call site.
    # The contract test asserts they are unimplemented on purpose, so wiring up a
    # worker for one will fail that test until this list is updated.
    PARSE_JOB_POSTING = "parse_job_posting"
    COMPUTE_JOB_MATCH = "compute_job_match"
    EMBED_BULLET = "embed_bullet"


#: Task names deliberately declared ahead of their worker implementations.
#: Anything here must not be enqueued. Keep in sync with the comment above.
UNIMPLEMENTED_TASKS: frozenset[str] = frozenset(
    {
        TaskName.PARSE_JOB_POSTING.value,
        TaskName.COMPUTE_JOB_MATCH.value,
        TaskName.EMBED_BULLET.value,
    }
)


_render_pool: ArqRedis | None = None
_automation_pool: ArqRedis | None = None


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_uri)


async def connect_queues() -> None:
    """Open both queue pools. Called from the app lifespan."""
    global _render_pool, _automation_pool
    settings = get_settings()
    if _render_pool is None:
        _render_pool = await create_pool(
            _redis_settings(), default_queue_name=settings.render_queue_name
        )
    if _automation_pool is None:
        _automation_pool = await create_pool(
            _redis_settings(), default_queue_name=settings.automation_queue_name
        )
    logger.info("queues.connected")


async def disconnect_queues() -> None:
    global _render_pool, _automation_pool
    for pool in (_render_pool, _automation_pool):
        if pool is not None:
            await pool.aclose()
    _render_pool = None
    _automation_pool = None
    logger.info("queues.disconnected")


def render_queue() -> ArqRedis:
    if _render_pool is None:
        raise RuntimeError("render queue not initialised -- connect_queues() runs in lifespan")
    return _render_pool


def automation_queue() -> ArqRedis:
    if _automation_pool is None:
        raise RuntimeError("automation queue not initialised -- connect_queues() runs in lifespan")
    return _automation_pool


async def enqueue_render(
    *, version_id: str, user_id: str, template: str, job_id: str | None = None
) -> str | None:
    """Queue a LaTeX compile. Returns the ARQ job id.

    `_job_id` is derived from the version id, which makes the enqueue
    idempotent: re-submitting the same version while a compile is pending is a
    no-op and returns None.
    """
    job = await render_queue().enqueue_job(
        TaskName.RENDER_RESUME.value,
        version_id=version_id,
        user_id=user_id,
        template=template,
        _job_id=f"render:{version_id}",
        _queue_name=get_settings().render_queue_name,
    )
    if job is None:
        logger.info("queue.render_already_pending", version_id=version_id)
        return None
    logger.info("queue.render_enqueued", version_id=version_id, arq_job_id=job.job_id)
    return job.job_id


async def enqueue_submission(
    *, application_id: str, user_id: str, defer_seconds: int = 0, approved: bool = False
) -> str | None:
    """Queue an application submission.

    `defer_seconds` supports deliberate jitter between submissions to the same
    board. Firing twenty applications at one ATS in ten seconds is the
    clearest possible bot signal.

    `approved` is set only by the API's explicit approval endpoint: it is the
    one thing that bypasses the worker's human-in-the-loop gate, and the
    worker replays the whole pipeline from navigation rather than resuming a
    held browser.
    """
    job = await automation_queue().enqueue_job(
        TaskName.SUBMIT_APPLICATION.value,
        application_id=application_id,
        user_id=user_id,
        approved=approved,
        _job_id=f"submit:{application_id}",
        _defer_by=defer_seconds or None,
        _queue_name=get_settings().automation_queue_name,
    )
    if job is None:
        logger.info("queue.submission_already_pending", application_id=application_id)
        return None
    logger.info("queue.submission_enqueued", application_id=application_id, arq_job_id=job.job_id)
    return job.job_id


async def enqueue_score(
    *, version_id: str, user_id: str, job_id: str | None = None
) -> str | None:
    """Queue an ATS scoring run.

    Scoring runs on the render queue because it needs the compiled PDF -- it
    extracts text from the artifact, so it must run where the artifact lives.
    """
    job = await render_queue().enqueue_job(
        TaskName.SCORE_RESUME.value,
        version_id=version_id,
        user_id=user_id,
        job_id=job_id,
        _job_id=f"score:{version_id}:{job_id or 'generic'}",
        _queue_name=get_settings().render_queue_name,
    )
    return job.job_id if job else None


async def queue_health() -> dict[str, Any]:
    """Queue depth and worker liveness for the readiness probe."""
    settings = get_settings()
    try:
        redis = render_queue()
        return {
            "reachable": True,
            "render_queued": await redis.zcard(f"arq:queue:{settings.render_queue_name}"),
            "automation_queued": await automation_queue().zcard(
                f"arq:queue:{settings.automation_queue_name}"
            ),
        }
    except Exception as exc:  # noqa: BLE001 - health checks must never raise
        return {"reachable": False, "error": str(exc)}
