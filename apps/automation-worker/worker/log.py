"""Logging setup for the automation worker.

Named `log.py` rather than `logging.py` on purpose: a module named `logging`
inside the package is legal under absolute imports but makes every `import
logging` in this codebase a thing you have to think about, and thinking about it
once per file is a tax with no upside.

The interesting part is `_scrub`. This worker is the one process that holds
plaintext job-board credentials, and structlog's default behaviour is to
serialise whatever it is handed. `crypto.BoardLogin` defends the obvious path by
overriding `__repr__`, but a password reaches a log through less obvious ones: a
board echoing it back in an error body, a Playwright timeout message quoting the
`fill()` argument, or an f-string added to a debug line at 2am. A processor at
the bottom of the chain is the only place that sees *every* event regardless of
which of those happened.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from worker.settings import get_settings

# Keys whose values never belong in a log line, matched case-insensitively as
# substrings so `board_password` and `passwordConfirm` are both caught.
_SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "encryption_key",
    "encrypted_blob",
    "authorization",
    "cookie",
    "session_id",
    "api_key",
)

_REDACTED = "***"


def _scrub(
    _logger: Any, _method: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Redact sensitive values by key name, recursively.

    Key-based and not value-based: scanning values for things that look like
    secrets is unreliable in both directions, whereas a field literally named
    `password` is unambiguous. Nested dicts are walked because the natural way to
    log a credential document is to hand structlog the whole dict.
    """
    return {key: _scrub_value(key, value) for key, value in event_dict.items()}


def _scrub_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
        return _REDACTED
    if isinstance(value, dict):
        return {k: _scrub_value(k, v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        # The key does not change for sequence members, so a list under a
        # sensitive key is already handled above; this only recurses into dicts
        # nested in a non-sensitive list.
        return type(value)(_scrub_value(key, v) for v in value)
    return value


def configure_logging() -> None:
    """Install the processor chain. Idempotent; safe to call from ARQ startup."""
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(message)s")

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # Last before rendering: every other processor has finished adding keys,
        # so nothing can introduce a secret after this point.
        _scrub,
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
