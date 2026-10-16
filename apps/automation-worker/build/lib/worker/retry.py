"""Retry policy, per-board rate limiting, and the circuit breaker.

The governing rule, which inverts the usual reflex:

    **A block is never retried.**

Retrying through a bot wall is worse than giving up. It escalates a soft
challenge into an IP-range ban, and because egress is shared, the ban lands on
every user of the platform rather than the one whose submission failed. So
`AttemptOutcome.BLOCKED` is terminal, full stop, and the recovery path is a human
being -- not a longer backoff.

Three mechanisms, in increasing scope:

* **Backoff** bounds retries for one application against transient faults.
* **Rate limiting** bounds how often *any* submission touches a given board,
  across all users, because volume is what triggers defences in the first place.
* **The circuit breaker** stops the worker from grinding a queue of fifty
  applications against a board that has started challenging everything.

State lives in Redis rather than in process memory: the limits must hold across
worker replicas and survive restarts, and an in-memory counter resets to zero
exactly when a deploy happens during an incident.
"""

from __future__ import annotations

import random
import time
from enum import StrEnum

import structlog
from arq.connections import ArqRedis

from worker.settings import get_settings

logger = structlog.get_logger(__name__)


class AttemptOutcome(StrEnum):
    """Mirrors `apps/api/app/models/application.py::AttemptOutcome`."""

    SUCCESS = "success"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"


# Only one outcome is retryable, and it is enumerated as a set rather than as a
# negation so that adding a new outcome defaults to *not* retrying. Failing
# closed is the right default when the side effect is irreversible.
_RETRYABLE: frozenset[AttemptOutcome] = frozenset({AttemptOutcome.RETRYABLE_FAILURE})


def should_retry(outcome: AttemptOutcome, attempt_number: int) -> bool:
    """Whether to schedule another attempt."""
    settings = get_settings()
    if outcome not in _RETRYABLE:
        return False
    return attempt_number < settings.automation_max_attempts


def backoff_seconds(attempt_number: int) -> float:
    """Exponential backoff with jitter over the upper half of the window.

    Two competing concerns, and this lands between them. Jitter has to be wide
    enough to de-correlate: when a board has a brief outage every queued
    submission fails at once, and a narrow jitter band re-synchronises them into a
    second burst that the board reads as coordinated. But textbook full jitter
    (`uniform(0, computed)`) can return a delay of half a second against a board
    that just failed, which is the opposite of backing off.

    So the range is `[capped/2, capped]`: a factor-of-two spread is ample to break
    up a herd, while the floor guarantees the delay actually grows with each
    attempt.
    """
    settings = get_settings()
    computed = settings.automation_backoff_base_seconds * (2 ** (attempt_number - 1))
    capped = min(computed, settings.automation_backoff_max_seconds)
    return random.uniform(capped * 0.5, capped)  # noqa: S311 - scheduling, not secrecy


# --- Per-board pacing -------------------------------------------------------


def _pace_key(board: str) -> str:
    return f"resunet:automation:lastseen:{board}"


def _breaker_key(board: str) -> str:
    return f"resunet:automation:breaker:{board}"


async def seconds_until_board_available(redis: ArqRedis, board: str) -> float:
    """How long to wait before touching this board again. 0 means go now.

    Deliberately advisory rather than a lock: the caller defers the job instead
    of sleeping, so a paced-out submission returns the worker to the pool for
    other boards rather than occupying a slot doing nothing.
    """
    settings = get_settings()
    raw = await redis.get(_pace_key(board))
    if raw is None:
        return 0.0
    last = float(raw)
    elapsed = time.time() - last
    remaining = settings.automation_min_seconds_between_board_hits - elapsed
    return max(0.0, remaining)


async def mark_board_hit(redis: ArqRedis, board: str) -> None:
    """Record that we just interacted with a board.

    Called before navigation, not after success. A failed attempt consumed a
    request against the board's defences just as a successful one did, so it
    must count toward the pacing budget.
    """
    settings = get_settings()
    await redis.set(
        _pace_key(board),
        str(time.time()),
        # Expiry slightly beyond the window so the key self-cleans for boards
        # that go quiet, rather than accumulating one entry per board forever.
        ex=int(settings.automation_min_seconds_between_board_hits * 3) + 10,
    )


# --- Circuit breaker --------------------------------------------------------


class CircuitOpenError(RuntimeError):
    """The board's circuit is open; no attempt was made."""

    def __init__(self, board: str, retry_after_seconds: float) -> None:
        super().__init__(
            f"circuit open for board {board!r}; retry in {retry_after_seconds:.0f}s"
        )
        self.board = board
        self.retry_after_seconds = retry_after_seconds


async def check_circuit(redis: ArqRedis, board: str) -> None:
    """Raise if the board is currently short-circuited."""
    settings = get_settings()
    failures = await redis.get(_breaker_key(board))
    if failures is None:
        return
    if int(failures) >= settings.automation_board_failure_threshold:
        ttl = await redis.ttl(_breaker_key(board))
        raise CircuitOpenError(board, float(max(ttl, 0)))


async def record_board_failure(redis: ArqRedis, board: str) -> int:
    """Increment the failure counter and return the new count.

    The TTL is set on every increment, so the window slides: a board failing
    steadily keeps the circuit open, while one that recovers ages out naturally
    without needing a separate reset path.
    """
    settings = get_settings()
    key = _breaker_key(board)
    count = int(await redis.incr(key))
    await redis.expire(key, int(settings.automation_circuit_cooldown_seconds))
    if count == settings.automation_board_failure_threshold:
        logger.error(
            "automation.circuit_opened",
            board=board,
            failures=count,
            cooldown_seconds=settings.automation_circuit_cooldown_seconds,
            detail="Consecutive failures hit the threshold. Investigate before "
            "reopening -- this usually means the form changed or the board "
            "started challenging us.",
        )
    return count


async def record_board_success(redis: ArqRedis, board: str) -> None:
    """Clear the failure counter.

    Reset rather than decrement: the counter is tracking *consecutive* failures,
    and one success means the board is answering normally again.
    """
    await redis.delete(_breaker_key(board))
