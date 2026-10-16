"""FastAPI dependencies: auth, pagination, ownership guards.

`CurrentUser` is the important one. It resolves the bearer token *and*
re-reads the user document to check status and token version, so a suspended
account or a revoked session stops working immediately rather than at token
expiry. That is one extra indexed read per request -- worth it, because the
alternative is a 30-minute window where a banned user still has access.
"""

from __future__ import annotations

from typing import Annotated, Any

from bson import ObjectId
from fastapi import Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import NotFoundError, PermissionDeniedError, UnauthorizedError
from app.core.security import AuthError, decode_token
from app.db.mongo import Collections, collection
from app.models.user import User, UserRole, UserStatus

# auto_error=False so a missing header raises our own error envelope rather
# than FastAPI's, keeping every 401 shaped identically for the frontend.
_bearer = HTTPBearer(auto_error=False, description="JWT access token")


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> User:
    """Resolve the authenticated user, or raise 401."""
    if credentials is None:
        raise UnauthorizedError("Missing bearer token.")

    try:
        claims = decode_token(credentials.credentials, expected_type="access")
    except AuthError as exc:
        raise UnauthorizedError(str(exc)) from exc

    user_id = claims.get("sub")
    if not user_id or not ObjectId.is_valid(user_id):
        raise UnauthorizedError("Malformed token subject.")

    doc = await collection(Collections.USERS).find_one({"_id": ObjectId(user_id)})
    if doc is None:
        raise UnauthorizedError("Account no longer exists.")

    user = User.model_validate(doc)

    if user.status is not UserStatus.ACTIVE:
        raise UnauthorizedError(f"Account is {user.status.value}.")

    # Revocation check: a bumped token_version invalidates older tokens
    # without maintaining a denylist.
    if claims.get("token_version") != user.token_version:
        raise UnauthorizedError("Session has been revoked. Sign in again.")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(user: CurrentUser) -> User:
    if UserRole.ADMIN not in user.roles:
        raise PermissionDeniedError()
    return user


AdminUser = Annotated[User, Depends(require_admin)]


class Pagination:
    """Shared limit/offset params with a hard ceiling.

    The ceiling is not negotiable: an unbounded `limit` is how one client
    request turns into an out-of-memory event.
    """

    def __init__(
        self,
        limit: Annotated[int, Query(ge=1, le=100, description="Max items to return.")] = 20,
        offset: Annotated[int, Query(ge=0, description="Items to skip.")] = 0,
    ) -> None:
        self.limit = limit
        self.offset = offset


PaginationParams = Annotated[Pagination, Depends(Pagination)]


async def fetch_owned(
    coll: Collections,
    doc_id: str,
    user: User,
    *,
    user_field: str = "user_id",
) -> dict[str, Any]:
    """Load a document and assert the caller owns it.

    Ownership is part of the query filter, not a check after the fetch. That
    ordering matters: it makes it impossible to write a handler that reads
    someone else's document and forgets to compare ids afterward.
    """
    if not ObjectId.is_valid(doc_id):
        raise NotFoundError()

    doc = await collection(coll).find_one(
        {"_id": ObjectId(doc_id), user_field: ObjectId(str(user.id))}
    )
    if doc is None:
        # Indistinguishable from "not yours" by design -- see PermissionDeniedError.
        raise NotFoundError()
    return doc
