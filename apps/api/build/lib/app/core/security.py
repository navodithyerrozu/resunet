"""Password hashing and JWT issuance/verification.

Argon2id is the hash, not bcrypt: it is memory-hard, so GPU cracking is far
more expensive per guess, and it has no 72-byte silent truncation.

Access tokens are short-lived and stateless. Refresh tokens carry a
`token_version` claim checked against the user document, which gives us
revocation without a denylist: bumping `User.token_version` invalidates every
outstanding refresh token for that user in one write.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal, cast

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings
from app.models.base import utcnow

_pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
    # ~50ms per hash on typical server hardware: slow enough to frustrate
    # offline cracking, fast enough not to be its own DoS vector.
    argon2__time_cost=3,
    argon2__memory_cost=65536,
    argon2__parallelism=4,
)

TokenType = Literal["access", "refresh"]


class AuthError(Exception):
    """Raised on any token or credential failure.

    Deliberately carries no detail about *which* check failed. Distinguishing
    "no such user" from "wrong password" hands an attacker an account
    enumeration oracle.
    """


def hash_password(password: str) -> str:
    return cast(str, _pwd_context.hash(password))


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time-ish verification via passlib.

    Returns False rather than raising on a malformed hash, so a corrupt row
    fails closed instead of 500-ing the login endpoint.
    """
    try:
        return cast(bool, _pwd_context.verify(plain, hashed))
    except (ValueError, TypeError):
        return False


def needs_rehash(hashed: str) -> bool:
    """Whether a stored hash uses outdated parameters.

    Call after a successful login and transparently upgrade the stored hash;
    that is how you migrate cost parameters without a password reset.
    """
    return cast(bool, _pwd_context.needs_update(hashed))


def _create_token(
    *,
    subject: str,
    token_version: int,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = utcnow()
    payload: dict[str, Any] = {
        "sub": subject,
        "token_version": token_version,
        "typ": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if extra_claims:
        payload.update(extra_claims)
    settings = get_settings()
    return cast(
        str, jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    )


def create_access_token(
    *, subject: str, token_version: int, roles: list[str] | None = None
) -> str:
    settings = get_settings()
    return _create_token(
        subject=subject,
        token_version=token_version,
        token_type="access",  # noqa: S106 - JWT claim discriminator, not a credential.
        expires_delta=timedelta(minutes=settings.access_token_ttl_minutes),
        extra_claims={"roles": roles or ["user"]},
    )


def create_refresh_token(*, subject: str, token_version: int) -> str:
    settings = get_settings()
    return _create_token(
        subject=subject,
        token_version=token_version,
        token_type="refresh",  # noqa: S106 - JWT claim discriminator, not a credential.
        expires_delta=timedelta(days=settings.refresh_token_ttl_days),
    )


def decode_token(token: str, *, expected_type: TokenType = "access") -> dict[str, Any]:
    """Decode and validate a JWT.

    Enforces the token type so a refresh token cannot be presented as a
    bearer credential -- that confusion is a real and commonly exploited bug,
    since a refresh token's long TTL makes it a much more valuable capture.
    """
    settings = get_settings()
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require_exp": True, "require_sub": True},
        )
    except JWTError as exc:
        raise AuthError("invalid or expired token") from exc

    if claims.get("typ") != expected_type:
        raise AuthError("wrong token type")
    return claims


def token_expiry_seconds() -> int:
    return get_settings().access_token_ttl_minutes * 60


def is_expired(claims: dict[str, Any]) -> bool:
    exp = claims.get("exp")
    return exp is None or datetime.fromtimestamp(exp, tz=utcnow().tzinfo) <= utcnow()
