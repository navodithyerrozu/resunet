"""Security behavior for the authentication endpoints."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId
from jose import jwt
from pymongo.errors import DuplicateKeyError

from app.api.v1.routes import auth
from app.core.config import get_settings
from app.core.errors import ConflictError, UnauthorizedError
from app.core.security import hash_password
from app.models.base import utcnow
from app.models.user import User, UserStatus


class FakeUsers:
    def __init__(self, document: dict[str, Any] | None) -> None:
        self.document = document

    async def find_one(self, _query: dict[str, Any]) -> dict[str, Any] | None:
        return self.document


def _user_doc(*, status: UserStatus = UserStatus.ACTIVE, token_version: int = 1) -> dict[str, Any]:
    user = User(
        _id=ObjectId("111111111111111111111111"),
        email="user@example.com",
        password_hash=hash_password("correct horse battery staple"),
        full_name="Test User",
        status=status,
        token_version=token_version,
    )
    return user.to_mongo()


def _bypass_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(auth, "enforce_auth_rate_limit", noop)


@pytest.mark.asyncio
async def test_login_missing_account_uses_generic_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bypass_limiter(monkeypatch)
    monkeypatch.setattr(auth, "collection", lambda _name: FakeUsers(None))

    with pytest.raises(UnauthorizedError, match="Invalid email or password"):
        await auth.login(
            auth.UserLogin(email="unknown@example.com", password="wrong"),  # noqa: S106
        )


@pytest.mark.asyncio
async def test_login_rejects_wrong_password_and_inactive_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bypass_limiter(monkeypatch)
    user_doc = _user_doc()
    monkeypatch.setattr(auth, "collection", lambda _name: FakeUsers(user_doc))
    with pytest.raises(UnauthorizedError, match="Invalid email or password"):
        await auth.login(
            auth.UserLogin(email="user@example.com", password="wrong password"),  # noqa: S106
        )

    inactive_doc = _user_doc(status=UserStatus.SUSPENDED)
    monkeypatch.setattr(auth, "collection", lambda _name: FakeUsers(inactive_doc))
    with pytest.raises(UnauthorizedError, match="Account is suspended"):
        await auth.login(
            auth.UserLogin(  # noqa: S106
                email="user@example.com",
                password="correct horse battery staple",  # noqa: S106
            ),
        )


@pytest.mark.asyncio
async def test_refresh_rejects_expired_token() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    expired_token = jwt.encode(
        {
            "sub": "111111111111111111111111",
            "token_version": 1,
            "typ": "refresh",
            "iat": int((now - timedelta(minutes=2)).timestamp()),
            "exp": int((now - timedelta(minutes=1)).timestamp()),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(UnauthorizedError, match="invalid or expired token"):
        await auth.refresh(expired_token)


@pytest.mark.asyncio
async def test_refresh_rejects_revoked_token_version(monkeypatch: pytest.MonkeyPatch) -> None:
    user_doc = _user_doc(token_version=2)
    monkeypatch.setattr(auth, "collection", lambda _name: FakeUsers(user_doc))
    monkeypatch.setattr(
        auth,
        "decode_token",
        lambda *_args, **_kwargs: {
            "sub": str(user_doc["_id"]),
            "token_version": 1,
            "typ": "refresh",
            "exp": int((utcnow() + timedelta(minutes=1)).timestamp()),
        },
    )

    with pytest.raises(UnauthorizedError, match="Session has been revoked"):
        await auth.refresh("previous-session")


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    @asynccontextmanager
    async def start_transaction(self):
        yield


class _DuplicateUsers:
    async def insert_one(self, *_args: Any, **_kwargs: Any) -> None:
        raise DuplicateKeyError("duplicate email")


class _LogoutUsers:
    def __init__(self) -> None:
        self.filter: dict[str, Any] | None = None
        self.update: dict[str, Any] | None = None

    async def update_one(self, filt: dict[str, Any], update: dict[str, Any]) -> None:
        self.filter = filt
        self.update = update


@pytest.mark.asyncio
async def test_logout_increments_token_version_for_the_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    users = _LogoutUsers()
    monkeypatch.setattr(auth, "collection", lambda _name: users)
    user = User(
        _id=ObjectId("111111111111111111111111"),
        email="user@example.com",
        password_hash="not-used-in-this-test",  # noqa: S106
        full_name="Test User",
        status=UserStatus.ACTIVE,
    )

    await auth.logout(user)

    assert users.filter == {"_id": ObjectId("111111111111111111111111")}
    assert users.update == {"$inc": {"token_version": 1}}


@pytest.mark.asyncio
async def test_register_maps_duplicate_email_to_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    _bypass_limiter(monkeypatch)
    monkeypatch.setattr(auth, "collection", lambda _name: _DuplicateUsers())

    async def start_session() -> _FakeSession:
        return _FakeSession()

    monkeypatch.setattr(auth, "get_client", lambda: SimpleNamespace(start_session=start_session))

    with pytest.raises(ConflictError, match="already exists"):
        await auth.register(
            auth.UserCreate(
                email="user@example.com",
                password="correct horse battery staple",  # noqa: S106
                full_name="Test User",
            ),
        )

