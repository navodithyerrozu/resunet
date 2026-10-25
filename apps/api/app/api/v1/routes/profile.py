"""Career profile routes: the master record the resume agent draws from.

The `raw_notes` field on each experience entry is the highest-leverage thing
in this module. It is where the user dumps unstructured recollection ("rebuilt
the retry logic, think it cut failures a lot, used Redis"), and it is the
primary input to the generation agent. Encourage messy, generous input here --
the agent's job is to interrogate it into XYZ form, and it cannot invent a
metric the user never mentioned.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, status
from pymongo import ReturnDocument

from app.api.deps import CurrentUser
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.db.mongo import Collections, collection
from app.models.base import utcnow
from app.models.bullet import BulletStatus
from app.models.profile import CareerProfile, ExperienceEntry, SkillCount
from app.models.user import User

router = APIRouter(prefix="/profile", tags=["profile"])
logger = get_logger(__name__)


def _user_oid(user: User) -> ObjectId:
    return ObjectId(str(user.id))


def _owner_filter(user: User) -> dict[str, object]:
    """Match this user's profile whether `user_id` was stored as ObjectId or str.

    Registration writes ObjectId. Ownership is the query filter, not a check
    after the fetch, so another user's document cannot be loaded by accident.
    """
    oid = _user_oid(user)
    return {"user_id": {"$in": [oid, str(oid)]}}


def _bson_safe(value: Any) -> Any:
    """Make a Pydantic dump legal for Motor (dates, enums, HttpUrl)."""
    if value is None or isinstance(value, (str, int, float, bool, bytes, ObjectId, datetime)):
        return value
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _bson_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bson_safe(item) for item in value]
    return str(value)


def profile_to_mongo(profile: CareerProfile) -> dict[str, Any]:
    """Persist a profile with BSON ObjectIds on `_id` and `user_id`."""
    doc = _bson_safe(profile.model_dump(by_alias=True, exclude_none=True))
    doc["user_id"] = ObjectId(str(profile.user_id))
    # `by_alias` now yields `id` (serialization alias); storage needs `_id`
    # only, and leaving both keys makes re-validation reject `id` as extra.
    doc.pop("id", None)
    if profile.id:
        doc["_id"] = ObjectId(str(profile.id))
    else:
        doc.pop("_id", None)
    return doc


async def load_owned_profile(user: User) -> CareerProfile:
    """Load the caller's career profile, or 404.

    A missing document after registration is a data-integrity bug, not an
    empty state -- we log it and refuse to invent a blank profile.
    """
    doc = await collection(Collections.CAREER_PROFILES).find_one(_owner_filter(user))
    if doc is None:
        logger.error("profile.missing", user_id=str(user.id))
        raise NotFoundError("Career profile not found.")
    return CareerProfile.model_validate(doc)


async def archive_bullets_for_entry(user: User, entry_id: str) -> int:
    """Mark bullets for a removed experience/project as archived.

    Never delete: a submitted resume version may still point at these rows.
    Matching both ObjectId and string covers mixed historical storage.
    """
    user_oid = _user_oid(user)
    entry_values: list[object] = [entry_id]
    if ObjectId.is_valid(entry_id):
        entry_values.append(ObjectId(entry_id))

    result = await collection(Collections.BULLETS).update_many(
        {
            "user_id": {"$in": [user_oid, str(user_oid)]},
            "profile_entry_id": {"$in": entry_values},
        },
        {"$set": {"status": BulletStatus.ARCHIVED.value, "updated_at": utcnow()}},
    )
    return int(result.modified_count)


@router.get(
    "/skills",
    response_model=list[SkillCount],
    summary="Skills evidenced by XYZ-compliant bullets",
)
async def list_evidenced_skills(user: CurrentUser) -> list[SkillCount]:
    """Aggregate skill names across every compliant bullet the caller owns.

    Only bullets with `violations: []` count: a skill listed on a bullet that
    failed XYZ has not been evidenced. Sort is count desc, then name asc, so
    the dashboard pile is stable across refreshes.
    """
    user_oid = _user_oid(user)
    pipeline: list[dict[str, object]] = [
        {
            "$match": {
                "user_id": {"$in": [user_oid, str(user_oid)]},
                "violations": {"$size": 0},
            }
        },
        {"$unwind": "$skills"},
        {
            "$project": {
                "name": {"$trim": {"input": {"$toString": "$skills"}}},
            }
        },
        {"$match": {"name": {"$ne": ""}}},
        {"$group": {"_id": "$name", "count": {"$sum": 1}}},
        {"$sort": {"count": -1, "_id": 1}},
        {"$project": {"_id": 0, "name": "$_id", "count": 1}},
    ]
    cursor = collection(Collections.BULLETS).aggregate(pipeline)
    rows = await cursor.to_list(length=500)
    return [SkillCount.model_validate(row) for row in rows]


@router.get("", response_model=CareerProfile, summary="Get the career profile")
async def get_profile(user: CurrentUser) -> CareerProfile:
    """Fetch the authenticated user's profile. Never another user's."""
    return await load_owned_profile(user)


@router.put("", response_model=CareerProfile, summary="Replace the career profile")
async def replace_profile(payload: CareerProfile, user: CurrentUser) -> CareerProfile:
    """Replace the caller's profile content.

    Identity fields on the payload (`user_id`, `_id`, timestamps, completeness)
    are ignored. Completeness is recomputed here. Entry ids the client sends
    back are kept so bullets stay attached.
    """
    existing = await load_owned_profile(user)

    incoming = payload.model_copy(
        update={
            "id": existing.id,
            "user_id": str(user.id),
            "created_at": existing.created_at,
            "updated_at": utcnow(),
        }
    ).with_preserved_entry_ids()
    incoming.completeness = incoming.compute_completeness()

    removed_ids = existing.bullet_parent_ids() - incoming.bullet_parent_ids()
    for entry_id in removed_ids:
        await archive_bullets_for_entry(user, entry_id)

    replaced = await collection(Collections.CAREER_PROFILES).find_one_and_replace(
        _owner_filter(user),
        profile_to_mongo(incoming),
        return_document=ReturnDocument.AFTER,
    )
    if replaced is None:
        logger.error("profile.missing_on_replace", user_id=str(user.id))
        raise NotFoundError("Career profile not found.")
    return CareerProfile.model_validate(replaced)


@router.post(
    "/experiences",
    response_model=ExperienceEntry,
    status_code=status.HTTP_201_CREATED,
    summary="Add an experience entry",
)
async def add_experience(payload: ExperienceEntry, user: CurrentUser) -> ExperienceEntry:
    """Append one experience with `$push` so a parallel edit of another field is safe."""
    existing = await load_owned_profile(user)

    entry = payload
    if existing.find_entry(entry.entry_id) is not None:
        entry = entry.model_copy(update={"entry_id": str(ObjectId())})

    updated = existing.model_copy(update={"experiences": [*existing.experiences, entry]})
    completeness = updated.compute_completeness()
    now = utcnow()

    result = await collection(Collections.CAREER_PROFILES).find_one_and_update(
        _owner_filter(user),
        {
            "$push": {"experiences": _bson_safe(entry.model_dump(exclude_none=True))},
            "$set": {"completeness": completeness, "updated_at": now},
        },
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        logger.error("profile.missing_on_experience_add", user_id=str(user.id))
        raise NotFoundError("Career profile not found.")
    return entry


@router.delete(
    "/experiences/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an experience entry",
)
async def delete_experience(entry_id: str, user: CurrentUser) -> None:
    """Pull one experience and archive (do not delete) its bullets."""
    existing = await load_owned_profile(user)
    if not any(item.entry_id == entry_id for item in existing.experiences):
        raise NotFoundError()

    remaining = [item for item in existing.experiences if item.entry_id != entry_id]
    completeness = existing.model_copy(update={"experiences": remaining}).compute_completeness()

    result = await collection(Collections.CAREER_PROFILES).update_one(
        _owner_filter(user),
        {
            "$pull": {"experiences": {"entry_id": entry_id}},
            "$set": {"completeness": completeness, "updated_at": utcnow()},
        },
    )
    if result.matched_count == 0:
        raise NotFoundError()

    await archive_bullets_for_entry(user, entry_id)
    return None
