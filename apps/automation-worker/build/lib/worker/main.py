"""ARQ entrypoint for the automation worker.

Two things here are deliberate and worth not "cleaning up":

**Concurrency defaults to 1.** A Chromium instance with one open context is
roughly 300-400MB, and the humanised delays mean a submission occupies its slot
for 30-90 seconds. Raising `max_jobs` without raising the container memory limit
gets the worker OOM-killed mid-submission -- the single worst moment for it to
die, because the form may already be submitted and the outcome is then unknown.

**`max_tries = 1`.** ARQ's own retry is switched off, not tuned. Retry policy for
this worker lives in `worker/retry.py`, which knows that a bot wall must never be
retried and that boards need pacing between attempts; ARQ knows neither. Letting
both mechanisms retry would multiply their attempt counts and defeat the
per-board circuit breaker. Tasks therefore *return* failures and re-enqueue
themselves with an explicit delay.
"""

from __future__ import annotations

from typing import Any

import structlog
from arq.connections import RedisSettings

from worker import browser as browser_mod
from worker import db
from worker.log import configure_logging
from worker.settings import get_settings
from worker.tasks.submit import submit_application

logger = structlog.get_logger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    """Open the long-lived resources: logging, Mongo, Playwright, the browser.

    The browser is launched once per process rather than per job. A launch costs
    1-2 seconds and a fixed slab of memory, which would otherwise be paid on
    every submission; contexts, which *are* per-job, are the cheap part.
    """
    configure_logging()
    settings = get_settings()
    db.connect()

    runtime = browser_mod.playwright_runtime()
    ctx["_playwright_cm"] = runtime
    ctx["playwright"] = await runtime.__aenter__()
    ctx["browser"] = await browser_mod.launch_browser(ctx["playwright"])

    settings.trace_storage_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "automation_worker.started",
        headless=settings.automation_headless,
        proxy_configured=bool(settings.automation_proxy_url),
        require_approval=settings.automation_require_approval,
        concurrency=settings.automation_concurrency,
    )


async def shutdown(ctx: dict[str, Any]) -> None:
    """Close everything, in reverse order, and never let teardown raise.

    A browser process that outlives the worker is not reaped by anything: it holds
    its memory and its open contexts until the container is killed. Each close is
    therefore individually guarded so one failure does not skip the rest.
    """
    for label, closer in (
        ("browser", _close_browser(ctx)),
        ("playwright", _close_playwright(ctx)),
        ("mongo", db.close()),
    ):
        try:
            await closer
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning("automation_worker.shutdown_failed", resource=label, error=str(exc))
    logger.info("automation_worker.stopped")


async def _close_browser(ctx: dict[str, Any]) -> None:
    browser = ctx.get("browser")
    if browser is not None:
        await browser.close()


async def _close_playwright(ctx: dict[str, Any]) -> None:
    runtime = ctx.get("_playwright_cm")
    if runtime is not None:
        await runtime.__aexit__(None, None, None)


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_uri)


class WorkerSettings:
    """ARQ picks this up via `arq worker.main.WorkerSettings`."""

    functions = [submit_application]
    on_startup = startup
    on_shutdown = shutdown
    # A plain attribute, not a method: ARQ reads these off the class and passes
    # them straight to `Worker(...)`, so a callable here arrives as a function
    # object where a RedisSettings is expected.
    redis_settings = _redis_settings()
    queue_name = get_settings().automation_queue_name

    max_jobs = get_settings().automation_concurrency
    # See the module docstring: retries are `retry.py`'s job, not ARQ's.
    max_tries = 1
    # Generous because the humanised delays are intentional and a slow board can
    # legitimately take minutes. The real timeout protection is per-action, in
    # `automation_action_timeout_ms`.
    job_timeout = get_settings().automation_job_timeout_seconds
    keep_result = 86_400  # a day: submission outcomes get inspected by humans
    health_check_interval = 30


# Task names cross a process boundary as opaque strings in a Redis payload. A
# rename on one side without the other produces jobs that sit in the queue until
# they expire, with no error raised anywhere -- so the mismatch is turned into an
# import-time crash instead.
EXPECTED_TASK_NAMES = frozenset({"submit_application"})
_registered = {fn.__name__ for fn in WorkerSettings.functions}
if _registered != EXPECTED_TASK_NAMES:  # pragma: no cover - import-time guard
    raise RuntimeError(
        f"task name drift: registered {sorted(_registered)}, "
        f"expected {sorted(EXPECTED_TASK_NAMES)}. "
        "Update apps/api/app/services/queue.py::TaskName to match."
    )
