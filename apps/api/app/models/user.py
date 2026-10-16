"""User identity and auth models.

Identity is deliberately thin and separate from career data (see
`app.models.profile`). This document is read on every authenticated request,
so it must stay small; a user's full work history has no business being
loaded to check a token.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import EmailStr, Field, field_validator

from app.models.base import ApiModel, MongoModel, PyObjectId


class UserStatus(StrEnum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class UserRole(StrEnum):
    USER = "user"
    ADMIN = "admin"


class User(MongoModel):
    """Collection: `users`."""

    email: EmailStr
    password_hash: str = Field(
        description="Argon2id hash. Excluded from every response model by construction -- "
        "there is no `UserPublic` field that maps to it."
    )
    full_name: str = Field(min_length=1, max_length=120)

    status: UserStatus = UserStatus.PENDING_VERIFICATION
    roles: list[UserRole] = Field(default_factory=lambda: [UserRole.USER])
    email_verified_at: datetime | None = None
    last_login_at: datetime | None = None

    token_version: int = Field(
        default=1, ge=1,
        description="Embedded in issued JWTs. Incrementing this invalidates every "
        "outstanding refresh token for the user -- the logout-everywhere and "
        "credential-rotation lever, without a token denylist.",
    )

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        """Lowercase so the unique index actually enforces uniqueness.

        Mongo's unique index is case-sensitive; without this, alice@x.com and
        Alice@x.com are two accounts.
        """
        return v.strip().lower()


# --- API contracts ---------------------------------------------------------


class UserCreate(ApiModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)


class UserLogin(ApiModel):
    email: EmailStr
    password: str


class UserPublic(ApiModel):
    """Safe outward projection. Contains no hash and no token version."""

    id: PyObjectId
    email: EmailStr
    full_name: str
    status: UserStatus
    roles: list[UserRole]
    created_at: datetime


class TokenPair(ApiModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a credential.
    expires_in: int = Field(description="Access-token lifetime in seconds.")


class TokenPayload(ApiModel):
    """Decoded JWT claims."""

    sub: PyObjectId
    token_version: int
    roles: list[UserRole] = Field(default_factory=list)
    exp: int
    iat: int
    typ: str = Field(default="access", description="'access' or 'refresh'.")
