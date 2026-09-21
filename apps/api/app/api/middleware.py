"""ASGI middleware: request id, timing, structured access logs."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import bind_request_context, clear_request_context, get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, bind it to the log context, and time the request.

    An inbound `X-Request-ID` is honoured so a trace can be followed from the
    Next.js server through the API and on into a worker job -- the workers
    read the same id off the enqueued job payload.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        bind_request_context(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Logged here rather than only in the exception handler so that
            # failures inside downstream middleware are still recorded.
            logger.exception(
                "request.unhandled_error",
                duration_ms=round((time.perf_counter() - start) * 1000, 2),
            )
            raise
        finally:
            clear_request_context()

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers["Server-Timing"] = f"app;dur={duration_ms}"

        # Health probes fire constantly; logging them buries real traffic.
        if not request.url.path.startswith("/health"):
            logger.info(
                "request.completed",
                status_code=response.status_code,
                duration_ms=duration_ms,
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline hardening headers.

    The API serves JSON only, so `nosniff` plus a deny-all CSP costs nothing
    and closes off content-sniffing and framing attacks against error pages.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path not in {"/docs", "/redoc"}:
            response.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; frame-ancestors 'none'",
            )
        return response
