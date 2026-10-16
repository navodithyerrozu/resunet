"""Redis-backed fixed-window limits for unauthenticated auth endpoints."""

from __future__ import annotations

from hashlib import sha256
from time import time

from app.core.config import get_settings
from app.core.errors import QueueUnavailableError, RateLimitedError
from app.services.queue import render_queue


async def enforce_auth_rate_limit(*, action: str, identity: str) -> None:
    """Limit attempts per route and normalized account identifier.

    Browser requests reach FastAPI through the same-origin BFF, so the API's
    network peer is the web server rather than the end user. Hashed identity
    keys avoid global throttling behind that proxy and keep email out of Redis.
    """
    identity_digest = sha256(identity.strip().casefold().encode("utf-8")).hexdigest()
    window = int(time() // 60)
    key = f"resunet:auth-limit:{action}:{identity_digest}:{window}"
    try:
        async with render_queue().pipeline(transaction=True) as pipeline:
            pipeline.incr(key)
            pipeline.expire(key, 60)
            count, _ = await pipeline.execute()
    except Exception as exc:  # noqa: BLE001 - fail closed when the shared limiter is unavailable
        raise QueueUnavailableError("Authentication rate limiting is unavailable.") from exc

    limit = get_settings().auth_rate_limit_per_minute
    if count > limit:
        raise RateLimitedError(
            f"Too many {action} attempts. Try again in a minute.",
            details={"retry_after_seconds": 60},
        )
