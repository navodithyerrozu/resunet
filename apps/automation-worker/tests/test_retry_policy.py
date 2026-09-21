"""Tests for retry policy, board pacing, and the circuit breaker.

These are safety tests. The property being pinned is not "backoff computes a
plausible number" -- it is that **a blocked attempt is never retried**, because
the cost of regressing that is an IP-range ban affecting every user of the
platform. The negative cases below therefore matter more than the positive ones.

Redis is replaced with an in-memory fake rather than a mock. A `MagicMock` would
happily accept `incr` on a key that was never set and return another mock,
which is exactly the behaviour the breaker logic depends on getting right.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from worker import retry
from worker.retry import AttemptOutcome


class FakeRedis:
    """Minimal in-memory stand-in for the handful of commands used.

    Expiry is tracked as an absolute deadline and honoured on read, so a TTL test
    does not need to sleep.
    """

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._expires: dict[str, float] = {}

    def _expired(self, key: str) -> bool:
        deadline = self._expires.get(key)
        if deadline is not None and time.time() >= deadline:
            self._values.pop(key, None)
            self._expires.pop(key, None)
            return True
        return False

    async def get(self, key: str) -> str | None:
        if self._expired(key):
            return None
        return self._values.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self._values[key] = value
        if ex is not None:
            self._expires[key] = time.time() + ex

    async def incr(self, key: str) -> int:
        self._expired(key)
        current = int(self._values.get(key, "0")) + 1
        self._values[key] = str(current)
        return current

    async def expire(self, key: str, seconds: int) -> None:
        self._expires[key] = time.time() + seconds

    async def ttl(self, key: str) -> int:
        deadline = self._expires.get(key)
        return -1 if deadline is None else max(0, int(deadline - time.time()))

    async def delete(self, key: str) -> None:
        self._values.pop(key, None)
        self._expires.pop(key, None)


@pytest.fixture
def redis() -> Any:
    return FakeRedis()


@pytest.fixture
def settings() -> Any:
    """The real settings object, with the cache cleared so env changes apply."""
    retry.get_settings.cache_clear()
    return retry.get_settings()


class TestRetryEligibility:
    """The core safety property: only transient failures are retried."""

    def test_retryable_failure_is_retried(self, settings: Any) -> None:
        assert retry.should_retry(AttemptOutcome.RETRYABLE_FAILURE, attempt_number=1)

    @pytest.mark.parametrize(
        "outcome",
        [
            AttemptOutcome.BLOCKED,
            AttemptOutcome.PERMANENT_FAILURE,
            AttemptOutcome.NEEDS_HUMAN,
            AttemptOutcome.SUCCESS,
        ],
    )
    def test_everything_else_is_terminal(
        self, outcome: AttemptOutcome, settings: Any
    ) -> None:
        assert retry.should_retry(outcome, attempt_number=1) is False

    def test_blocked_is_never_retried_at_any_attempt_number(self, settings: Any) -> None:
        """The single most important assertion in this file.

        A block means a bot wall was detected. Retrying escalates a soft challenge
        into a hard ban on shared egress, so no attempt number, backoff, or
        configuration makes it eligible.
        """
        for attempt in range(1, 20):
            assert retry.should_retry(AttemptOutcome.BLOCKED, attempt) is False

    def test_attempt_budget_is_exhausted(self, settings: Any) -> None:
        limit = settings.automation_max_attempts
        assert retry.should_retry(AttemptOutcome.RETRYABLE_FAILURE, limit - 1) is True
        assert retry.should_retry(AttemptOutcome.RETRYABLE_FAILURE, limit) is False
        assert retry.should_retry(AttemptOutcome.RETRYABLE_FAILURE, limit + 1) is False

    def test_new_outcomes_default_to_not_retrying(self) -> None:
        """`_RETRYABLE` is a positive set, so an unknown outcome fails closed.

        This is the structural reason the policy is safe to extend: adding a
        member to `AttemptOutcome` cannot accidentally make it retryable.
        """
        assert retry._RETRYABLE == frozenset({AttemptOutcome.RETRYABLE_FAILURE})
        assert retry.should_retry("some_future_outcome", 1) is False  # type: ignore[arg-type]


class TestBackoff:
    def test_delay_grows_with_attempt_number(self, settings: Any) -> None:
        # Compared as minimums because each call is randomised; the floor of the
        # jitter window is what must increase.
        first = min(retry.backoff_seconds(1) for _ in range(50))
        third = min(retry.backoff_seconds(3) for _ in range(50))
        assert third > first

    def test_delay_is_capped(self, settings: Any) -> None:
        for _ in range(50):
            assert retry.backoff_seconds(20) <= settings.automation_backoff_max_seconds

    def test_delay_never_collapses_to_zero(self, settings: Any) -> None:
        """The reason this implementation is not textbook full jitter.

        A retry that fires immediately against a board that just failed is not a
        backoff. The jitter floor is half the computed window, so the delay is
        always meaningfully non-zero.
        """
        base = settings.automation_backoff_base_seconds
        for _ in range(100):
            assert retry.backoff_seconds(1) >= base * 0.5

    def test_jitter_actually_varies(self, settings: Any) -> None:
        """A fixed delay would re-synchronise a herd of failed submissions."""
        observed = {round(retry.backoff_seconds(2), 4) for _ in range(30)}
        assert len(observed) > 1


@pytest.mark.asyncio
class TestBoardPacing:
    async def test_unseen_board_is_available_immediately(self, redis: Any) -> None:
        assert await retry.seconds_until_board_available(redis, "greenhouse") == 0.0

    async def test_recent_hit_forces_a_wait(self, redis: Any, settings: Any) -> None:
        await retry.mark_board_hit(redis, "greenhouse")
        wait = await retry.seconds_until_board_available(redis, "greenhouse")
        assert 0 < wait <= settings.automation_min_seconds_between_board_hits

    async def test_pacing_is_per_board(self, redis: Any) -> None:
        """A hit on one board must not throttle a different one."""
        await retry.mark_board_hit(redis, "greenhouse")
        assert await retry.seconds_until_board_available(redis, "lever") == 0.0

    async def test_key_carries_an_expiry(self, redis: Any) -> None:
        """Otherwise one key accumulates per board, forever."""
        await retry.mark_board_hit(redis, "greenhouse")
        assert await redis.ttl(retry._pace_key("greenhouse")) > 0


@pytest.mark.asyncio
class TestCircuitBreaker:
    async def test_closed_circuit_permits(self, redis: Any) -> None:
        await retry.check_circuit(redis, "greenhouse")  # must not raise

    async def test_opens_at_threshold_not_before(self, redis: Any, settings: Any) -> None:
        threshold = settings.automation_board_failure_threshold
        for _ in range(threshold - 1):
            await retry.record_board_failure(redis, "greenhouse")
        await retry.check_circuit(redis, "greenhouse")  # still closed

        await retry.record_board_failure(redis, "greenhouse")
        with pytest.raises(retry.CircuitOpenError):
            await retry.check_circuit(redis, "greenhouse")

    async def test_open_circuit_reports_a_retry_delay(
        self, redis: Any, settings: Any
    ) -> None:
        for _ in range(settings.automation_board_failure_threshold):
            await retry.record_board_failure(redis, "greenhouse")
        with pytest.raises(retry.CircuitOpenError) as exc_info:
            await retry.check_circuit(redis, "greenhouse")
        # The caller defers the job by this much, so it has to be usable.
        assert exc_info.value.retry_after_seconds > 0
        assert exc_info.value.board == "greenhouse"

    async def test_success_resets_rather_than_decrements(
        self, redis: Any, settings: Any
    ) -> None:
        """The counter tracks *consecutive* failures.

        Decrementing would leave a board that alternates failure/success slowly
        accumulating toward an open circuit despite working fine every other time.
        """
        for _ in range(settings.automation_board_failure_threshold - 1):
            await retry.record_board_failure(redis, "greenhouse")
        await retry.record_board_success(redis, "greenhouse")

        assert await redis.get(retry._breaker_key("greenhouse")) is None
        await retry.check_circuit(redis, "greenhouse")

    async def test_breaker_is_per_board(self, redis: Any, settings: Any) -> None:
        """One dead board must not stop submissions to every other board."""
        for _ in range(settings.automation_board_failure_threshold):
            await retry.record_board_failure(redis, "greenhouse")
        with pytest.raises(retry.CircuitOpenError):
            await retry.check_circuit(redis, "greenhouse")
        await retry.check_circuit(redis, "lever")  # unaffected

    async def test_failure_window_slides(self, redis: Any) -> None:
        """Every increment refreshes the TTL, so steady failure keeps it open."""
        await retry.record_board_failure(redis, "greenhouse")
        first = await redis.ttl(retry._breaker_key("greenhouse"))
        await retry.record_board_failure(redis, "greenhouse")
        second = await redis.ttl(retry._breaker_key("greenhouse"))
        assert second >= first
