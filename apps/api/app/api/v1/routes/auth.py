"""Auth routes: register, login, refresh, logout, me.

STUB: handlers validate input and return well-formed responses but do not
yet persist. The contract is real; the storage is not.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, status

from app.api.deps import CurrentUser
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    token_expiry_seconds,
)
from app.models.base import utcnow
from app.models.user import (
    TokenPair,
    UserCreate,
    UserLogin,
    UserPublic,
    UserRole,
    UserStatus,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_PLACEHOLDER_ID = "000000000000000000000000"


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
async def register(payload: UserCreate) -> UserPublic:
    """Register a new user.

    TODO: hash the password with `hash_password`, insert into `users`, and let
    the unique email index surface duplicates as ConflictError. Also create the
    user's empty CareerProfile in the same transaction -- every later code path
    assumes a profile exists, and creating it lazily means every read has to
    handle a missing one.
    """
    return UserPublic(
        id=_PLACEHOLDER_ID,
        email=payload.email,
        full_name=payload.full_name,
        status=UserStatus.PENDING_VERIFICATION,
        roles=[UserRole.USER],
        created_at=utcnow(),
    )


@router.post("/login", response_model=TokenPair, summary="Exchange credentials for tokens")
async def login(payload: UserLogin) -> TokenPair:
    """Authenticate and issue an access/refresh pair.

    TODO: look up by normalised email, `verify_password`, and raise the *same*
    UnauthorizedError for both unknown-user and bad-password. Run the hash
    verification even when the user does not exist, against a dummy hash, so
    response timing does not reveal which accounts are real.
    """
    return TokenPair(
        access_token=create_access_token(subject=_PLACEHOLDER_ID, token_version=1),
        refresh_token=create_refresh_token(subject=_PLACEHOLDER_ID, token_version=1),
        expires_in=token_expiry_seconds(),
    )


@router.post("/refresh", response_model=TokenPair, summary="Rotate an access token")
async def refresh(
    refresh_token: Annotated[str, Body(embed=True, description="A valid refresh token.")],
) -> TokenPair:
    """Issue a new pair from a refresh token.

    TODO: decode with `expected_type="refresh"`, then re-check `token_version`
    against the user document. Rotate the refresh token too (issue a new one,
    do not reuse) so a stolen token has a bounded useful life.
    """
    return TokenPair(
        access_token=create_access_token(subject=_PLACEHOLDER_ID, token_version=1),
        refresh_token=create_refresh_token(subject=_PLACEHOLDER_ID, token_version=1),
        expires_in=token_expiry_seconds(),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke every session for the current user",
)
async def logout(user: CurrentUser) -> None:
    """Invalidate all outstanding tokens.

    TODO: `$inc` the user's `token_version`. Every previously issued token
    then fails the version check on its next use -- global logout in one write,
    no denylist to maintain or expire.
    """
    return None


@router.get("/me", response_model=UserPublic, summary="Current user")
async def me(user: CurrentUser) -> UserPublic:
    """Return the authenticated user's public profile."""
    return UserPublic(
        id=str(user.id or _PLACEHOLDER_ID),
        email=user.email,
        full_name=user.full_name,
        status=user.status,
        roles=user.roles,
        created_at=user.created_at,
    )


@router.get("/config", summary="Public auth configuration", include_in_schema=False)
async def auth_config() -> dict[str, object]:
    """Client-visible auth parameters, so the frontend need not hardcode them."""
    settings = get_settings()
    return {
        "access_token_ttl_seconds": settings.access_token_ttl_minutes * 60,
        "refresh_token_ttl_seconds": settings.refresh_token_ttl_days * 86400,
        "min_password_length": 12,
    }
