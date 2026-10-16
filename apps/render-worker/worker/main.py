"""ARQ entrypoint for the render worker.

Run with: `arq worker.main.WorkerSettings`

`functions` registers task callables under the exact names the API enqueues.
Those strings are a cross-process contract -- see
`apps/api/app/services/queue.py::TaskName`. A rename on one side without the
other produces jobs that sit in Redis until they expire, with no error anywhere,
which is a genuinely unpleasant thing to debug. The assertion at the bottom of
this module is cheap insurance against that.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import structlog
from arq.connections import RedisSettings

from worker import db
from worker.settings import get_settings
from worker.tasks.render import render_resume_pdf
from worker.tasks.score import score_resume_ats

logger = structlog.get_logger(__name__)

# Names the API enqueues under. Kept as a literal set so the check below is
# independent of how the functions happen to be named in Python.
EXPECTED_TASK_NAMES = frozenset({"render_resume_pdf", "score_resume_ats"})


def _configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(message)s")
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer()
        if settings.is_production
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[settings.log_level.upper()]
        ),
        cache_logger_on_first_use=True,
    )


async def startup(ctx: dict[str, Any]) -> None:
    """Open shared resources once per worker process.

    Connecting here rather than per task matters: a Mongo handshake costs
    tens of milliseconds, which would otherwise be paid on every render.
    """
    _configure_logging()
    db.connect()
    ctx["settings"] = get_settings()
    logger.info(
        "render_worker.started",
        queue=get_settings().render_queue_name,
        concurrency=get_settings().render_concurrency,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    await db.close()
    logger.info("render_worker.stopped")


class WorkerSettings:
    """ARQ configuration.

    LaTeX failures are deterministic and are handled inside the task without
    raising. The bounded retries are for transient infrastructure faults.
    """

    functions: list[Callable[..., Any]] = [render_resume_pdf, score_resume_ats]
    on_startup = startup
    on_shutdown = shutdown

    # A plain attribute, not a `@staticmethod`: ARQ reads these off the class and
    # passes them straight to `Worker(...)`, so a callable arrives as a function
    # object where a RedisSettings is expected and the worker fails at boot.
    redis_settings = RedisSettings.from_dsn(get_settings().redis_uri)

    queue_name = get_settings().render_queue_name
    max_jobs = get_settings().render_concurrency
    job_timeout = get_settings().render_job_timeout_seconds
    max_tries = get_settings().render_max_tries

    # Results are kept an hour so the UI can poll for an outcome after a page
    # reload. Longer would accumulate PDF metadata in Redis for no benefit --
    # the durable record lives in `render_jobs`.
    keep_result = 3600

    # Health key is written to Redis so the readiness probe can distinguish
    # "no jobs queued" from "no worker running", which look identical from
    # queue depth alone.
    health_check_interval = 30


# Fail at import if the registered names drift from the API's contract.
_registered = {fn.__name__ for fn in WorkerSettings.functions}
if _registered != EXPECTED_TASK_NAMES:  # pragma: no cover - import-time guard
    raise RuntimeError(
        f"task name drift: registered {sorted(_registered)}, "
        f"expected {sorted(EXPECTED_TASK_NAMES)}. "
        "Update apps/api/app/services/queue.py::TaskName to match."
    )
