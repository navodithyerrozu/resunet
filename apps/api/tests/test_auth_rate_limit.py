from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import RateLimitedError
from app.services import auth_rate_limit


class FakePipeline:
    def __init__(self, count: int) -> None:
        self.count = count
        self.key: str | None = None
        self.expiry: int | None = None

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    def incr(self, key: str) -> FakePipeline:
        self.key = key
        return self

    def expire(self, key: str, seconds: int) -> FakePipeline:
        assert key == self.key
        self.expiry = seconds
        return self

    async def execute(self) -> tuple[int, bool]:
        return self.count, True


class FakeRedis:
    def __init__(self, count: int) -> None:
        self.pipeline_instance = FakePipeline(count)

    def pipeline(self, *, transaction: bool) -> FakePipeline:
        assert transaction
        return self.pipeline_instance


@pytest.mark.asyncio
async def test_auth_attempt_within_limit_hashes_identity_in_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis(count=3)
    monkeypatch.setattr(auth_rate_limit, "render_queue", lambda: redis)
    monkeypatch.setattr(
        auth_rate_limit, "get_settings", lambda: SimpleNamespace(auth_rate_limit_per_minute=3)
    )
    await auth_rate_limit.enforce_auth_rate_limit(
        action="login", identity=" Alice@Example.com "
    )

    assert redis.pipeline_instance.key is not None
    expected_digest = sha256(b"alice@example.com").hexdigest()
    assert redis.pipeline_instance.key.startswith(
        f"resunet:auth-limit:login:{expected_digest}:"
    )
    assert redis.pipeline_instance.expiry == 60


@pytest.mark.asyncio
async def test_auth_attempt_over_limit_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_rate_limit, "render_queue", lambda: FakeRedis(count=4))
    monkeypatch.setattr(
        auth_rate_limit, "get_settings", lambda: SimpleNamespace(auth_rate_limit_per_minute=3)
    )
    with pytest.raises(RateLimitedError):
        await auth_rate_limit.enforce_auth_rate_limit(
            action="register", identity="user@example.com"
        )
