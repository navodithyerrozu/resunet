"""FastAPI application factory and ASGI entrypoint.

`create_app()` is a factory rather than a module-level `app = FastAPI()`
because the OpenAPI codegen script (`scripts/gen-types.sh`) imports and calls
it to export the schema without booting Mongo or Redis. A module-level app
with eager connections would make schema generation require the whole stack.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.api.v1.router import api_router
from app.api.v1.routes import health
from app.core.config import get_settings
from app.core.errors import ResuNetError
from app.core.logging import configure_logging, get_logger
from app.db.mongo import connect, disconnect
from app.services.queue import connect_queues, disconnect_queues

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open and close shared resources around the app's lifetime.

    Connections are opened here rather than lazily on first use so that a
    misconfigured database URI fails at startup -- where an orchestrator will
    catch it and refuse the rollout -- instead of surfacing as a 500 on
    whichever request happens to arrive first.

    Indexes are deliberately NOT created here; see app/db/indexes.py for why
    that is an explicit deploy step.
    """
    settings = get_settings()
    configure_logging()
    logger.info(
        "app.starting",
        environment=settings.environment.value,
        database=settings.mongodb_db,
    )

    await connect()
    await connect_queues()
    logger.info("app.ready")

    try:
        yield
    finally:
        await disconnect_queues()
        from app.agents.checkpointer import reset_checkpointer

        reset_checkpointer()
        await disconnect()
        logger.info("app.stopped")


def create_app() -> FastAPI:
    """Build the ASGI app."""
    settings = get_settings()

    app = FastAPI(
        title=settings.project_name,
        version="0.1.0",
        description=(
            "Backend for ResuNet: conversational resume generation with enforced XYZ "
            "impact bullets, ATS-optimized LaTeX rendering, job matching, and "
            "automated application submission."
        ),
        lifespan=lifespan,
        docs_url=settings.docs_url,
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    # --- Middleware -------------------------------------------------------
    # Order matters and is inside-out: Starlette applies the last-added
    # middleware outermost. RequestContextMiddleware is added first so it wraps
    # closest to the route and can time the handler itself; CORS is added last
    # so it sits outermost and can answer preflights without touching the app.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Server-Timing"],
        max_age=600,
    )

    # --- Routers ----------------------------------------------------------
    # Health sits outside the version prefix: probes should not have to change
    # when the API version does.
    app.include_router(health.router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    # --- Exception handlers ----------------------------------------------
    @app.exception_handler(ResuNetError)
    async def handle_domain_error(request: Request, exc: ResuNetError) -> JSONResponse:
        """Map domain errors onto the shared error envelope.

        Client errors log at warning, server errors at error -- otherwise 404s
        from ordinary UI navigation drown out real failures.
        """
        log = logger.warning if exc.status_code < 500 else logger.error
        log("request.domain_error", code=exc.code, status_code=exc.status_code)
        return JSONResponse(status_code=exc.status_code, content=exc.to_payload())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Reshape Pydantic errors into our envelope.

        `str(err)` on the context because Pydantic can put non-serializable
        objects (like ValueError instances) in there, which would make the
        error response itself fail to serialize.
        """
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_failed",
                    "message": "Request validation failed.",
                    "details": {
                        "fields": [
                            {
                                "loc": [str(p) for p in err["loc"]],
                                "msg": err["msg"],
                                "type": err["type"],
                            }
                            for err in exc.errors()
                        ]
                    },
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """Normalise framework-raised HTTP errors into the same envelope."""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": f"http_{exc.status_code}",
                    "message": str(exc.detail),
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Last resort. Never leaks internals to the client.

        The traceback goes to the log with the request id; the client gets the
        request id and nothing else, so support can correlate without the
        response exposing stack frames or connection strings.
        """
        request_id = getattr(request.state, "request_id", None)
        logger.exception("request.unhandled_exception", request_id=request_id)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred.",
                    "details": {"request_id": request_id},
                }
            },
        )

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        return {
            "service": settings.project_name,
            "version": "0.1.0",
            "docs": settings.docs_url,
            "api": settings.api_v1_prefix,
        }

    return app


app = create_app()
