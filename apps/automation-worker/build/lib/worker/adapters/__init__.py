"""Adapter registry.

Resolution is ordered: specific adapters are tried in sequence and
`GenericAdapter` is the terminal fallback, because its `matches()` returns True
for everything. Registration order is therefore significant, and a new
vendor adapter goes *before* the generic one.
"""

from __future__ import annotations

import structlog

from worker.adapters.base import (
    AdapterError,
    ApplicantDetails,
    BoardAdapter,
    FieldMapping,
    SubmissionEvidence,
    SubmissionPayload,
    UnmappedFieldError,
)
from worker.adapters.generic import GenericAdapter
from worker.adapters.greenhouse import GreenhouseAdapter
from worker.adapters.lever import LeverAdapter

logger = structlog.get_logger(__name__)

# Order matters. Generic must stay last.
_ADAPTERS: tuple[BoardAdapter, ...] = (
    GreenhouseAdapter(),
    LeverAdapter(),
    GenericAdapter(),
)


def resolve_adapter(url: str) -> BoardAdapter:
    """Pick the adapter for a posting URL.

    Always returns something -- the generic fallback matches everything. That is
    a deliberate choice over raising: an unrecognised board is still worth
    attempting behind mandatory human approval, and a hard failure here would
    make the product useless outside two vendors.
    """
    for adapter in _ADAPTERS:
        if adapter.matches(url):
            if adapter.board == "generic":
                logger.info(
                    "adapter.generic_fallback",
                    url=url,
                    detail="No vendor adapter matched. Human approval is mandatory "
                    "for this submission.",
                )
            return adapter
    raise AssertionError("unreachable: GenericAdapter matches all URLs")


def adapter_for_board(board: str) -> BoardAdapter | None:
    """Look up an adapter by board name, for tests and admin tooling."""
    return next((a for a in _ADAPTERS if a.board == board), None)


def supported_boards() -> list[str]:
    """Boards with a dedicated adapter, excluding the fallback."""
    return [a.board for a in _ADAPTERS if a.board != "generic"]


__all__ = [
    "AdapterError",
    "ApplicantDetails",
    "BoardAdapter",
    "FieldMapping",
    "GenericAdapter",
    "GreenhouseAdapter",
    "LeverAdapter",
    "SubmissionEvidence",
    "SubmissionPayload",
    "UnmappedFieldError",
    "adapter_for_board",
    "resolve_adapter",
    "supported_boards",
]
