"""Structured logging.

structlog emits JSON in production so logs are queryable, and colourised
key-value pairs in development so they are readable. Every log line carries a
`request_id`, bound once by middleware, which is what makes it possible to
follow a single resume generation across the API, the queue, and both
workers.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import Environment, get_settings


def configure_logging() -> None:
    """Configure structlog and route stdlib logging through it. Idempotent."""
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.environment is Environment.DEVELOPMENT:
        renderer: Any = structlog.dev.ConsoleRenderer(colors=True)
    else:
        # format_exc_info only matters for JSON output; the console renderer
        # formats exceptions itself and would double-render.
        shared.append(structlog.processors.format_exc_info)
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)
    # Uvicorn's access log duplicates our request middleware; drop it.
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("pymongo", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Module-level logger."""
    return structlog.get_logger(name)  # type: ignore[no-any-return]


def bind_request_context(**kwargs: Any) -> None:
    """Attach values to every subsequent log line in this async context."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()
