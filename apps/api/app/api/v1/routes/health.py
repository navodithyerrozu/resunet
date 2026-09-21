"""Liveness and readiness probes.

Two endpoints, because they answer different questions and a load balancer
must not conflate them. `/health/live` asks "is this process running?" -- if
it fails, restart the container. `/health/ready` asks "can it serve traffic?"
-- if it fails, stop routing here but do NOT restart, because the problem is
almost always a dependency, and restarting on a Mongo blip turns a brief
degradation into a full crash-loop.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Response, status

from app.core.config import get_settings
from app.db.mongo import get_client
from app.services.queue import queue_health

router = APIRouter(prefix="/health", tags=["health"])

_STARTED_AT = time.time()


@router.get("/live", summary="Liveness probe")
async def live() -> dict[str, Any]:
    """Always 200 if the event loop is turning. Checks no dependencies."""
    return {
        "status": "alive",
        "uptime_seconds": round(time.time() - _STARTED_AT, 1),
        "environment": get_settings().environment.value,
    }


@router.get("/ready", summary="Readiness probe")
async def ready(response: Response) -> dict[str, Any]:
    """Verify Mongo and Redis are reachable.

    Returns 503 with a per-dependency breakdown when anything is down, so the
    failing component is visible in the probe output instead of requiring a
    log dive.
    """
    checks: dict[str, Any] = {}

    try:
        start = time.perf_counter()
        await get_client().admin.command("ping")
        checks["mongodb"] = {
            "ok": True,
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        }
    except Exception as exc:  # noqa: BLE001 - a probe must report, never raise
        checks["mongodb"] = {"ok": False, "error": str(exc)}

    q = await queue_health()
    checks["redis"] = {"ok": q.get("reachable", False), **q}

    all_ok = all(c.get("ok") for c in checks.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": "ready" if all_ok else "degraded", "checks": checks}
