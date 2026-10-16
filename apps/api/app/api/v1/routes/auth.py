"""Authentication routes: register, login, refresh, logout, me."""

from __future__ import annotations

from typing import Annotated

from bson import ObjectId
from fastapi import APIRouter, Body, status
from pymongo.errors import DuplicateKeyError

from app.api.deps import CurrentUser
from app.core.config import get_settings
from app.core.errors import ConflictError, UnauthorizedError
from app.core.security import (
    AuthError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    token_expiry_seconds,
    verify_password,
)
from app.db.mongo import Collections, collection, get_client
from app.models.profile import CareerProfile, ContactBlock
from app.models.user import (
    TokenPair,
    User,
    UserCreate,
    UserLogin,
    UserPublic,
    UserRole,
    UserStatus,
)
from app.services.auth_rate_limit import enforce_auth_rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# REGISTER
# ---------------------------------------------------------------------------

@router.post(
    "/register",
    response_model=TokenPair,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
async def register(payload: UserCreate) -> TokenPair:
    """Create a user and their empty career profile atomically, then sign them in."""
    await enforce_auth_rate_limit(action="register", identity=str(payload.email))

    # 1. Hash the password.
    #
    # The real password must NEVER be stored in MongoDB.
    password_hash = hash_password(payload.password)

    # 2. Create the User model.
    user = User(
        email=payload.email,
        password_hash=password_hash,
        full_name=payload.full_name,
        status=UserStatus.ACTIVE,
        roles=[UserRole.USER],
        token_version=1,
    )

    # 3. Get the shared MongoDB client.
    client = get_client()

    # 4. Create a transaction.
    #
    # Both the user and career profile must be created successfully.
    # If either operation fails, MongoDB rolls the transaction back.
    try:
        async with await client.start_session() as session, session.start_transaction():

            # 5. Insert the user.
            user_result = await collection(
                Collections.USERS
            ).insert_one(
                user.to_mongo(),
                session=session,
            )

            # 6. Create the user's initial empty career profile.
            profile = CareerProfile(
                user_id=str(user_result.inserted_id),
                contact=ContactBlock(
                    full_name=payload.full_name,
                    email=str(payload.email),
                ),
            )

            # Convert the Pydantic model into a MongoDB document.
            profile_doc = profile.to_mongo()

            # MongoDB relationship fields should use ObjectId.
            profile_doc["user_id"] = user_result.inserted_id

            # 7. Insert the career profile.
            await collection(
                Collections.CAREER_PROFILES
            ).insert_one(
                profile_doc,
                session=session,
            )
    except DuplicateKeyError as exc:
        raise ConflictError("An account with this email already exists.") from exc

    # 8. Issue a token pair so the caller can persist a session immediately.
    # password_hash is deliberately NOT returned.
    user_id = str(user_result.inserted_id)
    return TokenPair(
        access_token=create_access_token(subject=user_id, token_version=user.token_version),
        refresh_token=create_refresh_token(subject=user_id, token_version=user.token_version),
        expires_in=token_expiry_seconds(),
    )


# ---------------------------------------------------------------------------
# LOGIN
# ---------------------------------------------------------------------------

@router.post(
    "/login",
    response_model=TokenPair,
    summary="Authenticate and issue access/refresh tokens",
)
async def login(payload: UserLogin) -> TokenPair:
    """Authenticate a user and issue JWT access and refresh tokens."""
    await enforce_auth_rate_limit(action="login", identity=str(payload.email))

    # 1. Find the user by email.
    user_doc = await collection(
        Collections.USERS
    ).find_one(
        {"email": payload.email}
    )

    # 2. If the account doesn't exist, return the same error
    #    that we use for an incorrect password.
    if user_doc is None:
        raise UnauthorizedError("Invalid email or password.")

    # 3. Convert the MongoDB document into our User model.
    user = User.model_validate(user_doc)

    # 4. Check the supplied password against the stored password hash.
    if not verify_password(payload.password, user.password_hash):
        raise UnauthorizedError("Invalid email or password.")

    # 5. Do not allow inactive/suspended users to log in.
    if user.status is not UserStatus.ACTIVE:
        raise UnauthorizedError(
            f"Account is {user.status.value}."
        )

    # 6. Generate the access token.
    access_token = create_access_token(
        subject=str(user.id),
        token_version=user.token_version,
    )

    # 7. Generate the refresh token.
    refresh_token = create_refresh_token(
        subject=str(user.id),
        token_version=user.token_version,
    )

    # 8. Return both tokens.
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=token_expiry_seconds(),
    )


# ---------------------------------------------------------------------------
# REFRESH
# ---------------------------------------------------------------------------

@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Refresh an access/refresh token pair",
)
async def refresh(
    refresh_token: Annotated[str, Body(embed=True)],
) -> TokenPair:
    """Issue a new token pair from a valid refresh token."""

    # 1. Decode the refresh token.
    try:
        claims = decode_token(
            refresh_token,
            expected_type="refresh",
        )
    except AuthError as exc:
        raise UnauthorizedError(str(exc)) from exc

    # 2. Get the user ID from the JWT subject.
    user_id = claims.get("sub")

    if not user_id:
        raise UnauthorizedError("Malformed refresh token.")

    # 3. Look up the user.
    from bson import ObjectId

    if not ObjectId.is_valid(user_id):
        raise UnauthorizedError("Malformed refresh token.")

    user_doc = await collection(
        Collections.USERS
    ).find_one(
        {"_id": ObjectId(user_id)}
    )

    if user_doc is None:
        raise UnauthorizedError("Account no longer exists.")

    # 4. Convert MongoDB document into User model.
    user = User.model_validate(user_doc)

    # 5. Check account status.
    if user.status is not UserStatus.ACTIVE:
        raise UnauthorizedError(
            f"Account is {user.status.value}."
        )

    # 6. Check token version.
    #
    # If logout increased token_version, old refresh tokens
    # immediately become invalid.
    if claims.get("token_version") != user.token_version:
        raise UnauthorizedError(
            "Session has been revoked. Sign in again."
        )

    # 7. Create a fresh access token.
    access_token = create_access_token(
        subject=str(user.id),
        token_version=user.token_version,
    )

    # 8. Create a fresh refresh token.
    new_refresh_token = create_refresh_token(
        subject=str(user.id),
        token_version=user.token_version,
    )

    return TokenPair(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=token_expiry_seconds(),
    )


# ---------------------------------------------------------------------------
# LOGOUT
# ---------------------------------------------------------------------------

@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Log out and revoke existing tokens",
)
async def logout(user: CurrentUser) -> None:
    """Invalidate all existing tokens for the current user."""

    # Increase token_version.
    #
    # Every existing JWT contains the old token_version.
    # CurrentUser checks this value, so all old tokens become invalid.
    await collection(
        Collections.USERS
    ).update_one(
        {"_id": ObjectId(str(user.id))},
        {"$inc": {"token_version": 1}},
    )

    return


# ---------------------------------------------------------------------------
# CURRENT USER
# ---------------------------------------------------------------------------

@router.get(
    "/me",
    response_model=UserPublic,
    summary="Get the current authenticated user",
)
async def me(user: CurrentUser) -> UserPublic:
    """Return the currently authenticated user's public information."""

    return UserPublic(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        status=user.status,
        roles=user.roles,
        created_at=user.created_at,
    )


# ---------------------------------------------------------------------------
# AUTH CONFIG
# ---------------------------------------------------------------------------

@router.get(
    "/config",
    summary="Get authentication configuration",
)
async def auth_config() -> dict[str, int]:
    """Return authentication settings required by the frontend."""

    settings = get_settings()

    return {
        "access_token_ttl_seconds": (
            settings.access_token_ttl_minutes * 60
        ),
        "refresh_token_ttl_seconds": (
            settings.refresh_token_ttl_days * 86400
        ),
        "min_password_length": 12,
    }
