"""Shared Pydantic primitives: ObjectId handling, base models, enums.

Everything in the model layer inherits from here so that BSON `ObjectId`
round-trips cleanly through JSON, and so timestamps are consistently
timezone-aware UTC rather than a mix of naive and aware datetimes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from bson import ObjectId
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic.alias_generators import to_camel


def _validate_object_id(value: Any) -> str:
    """Coerce a BSON ObjectId (or its string form) into a plain string.

    Motor hands back real ObjectId instances. Pydantic cannot serialize those
    to JSON, so every id crossing the API boundary is normalised to `str`
    here rather than at each call site.
    """
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, str):
        if not ObjectId.is_valid(value):
            raise ValueError(f"invalid ObjectId: {value!r}")
        return value
    raise TypeError(f"ObjectId or str required, got {type(value).__name__}")


PyObjectId = Annotated[str, BeforeValidator(_validate_object_id)]
"""An ObjectId that is always a `str` in Python and JSON."""


def utcnow() -> datetime:
    """Timezone-aware UTC now.

    `datetime.utcnow()` returns a naive datetime, which compares incorrectly
    against the aware datetimes PyMongo returns when `tz_aware=True`. Always
    use this instead.
    """
    return datetime.now(UTC)


class AppModel(BaseModel):
    """Base for every model in the app."""

    model_config = ConfigDict(
        populate_by_name=True,
        use_enum_values=False,
        str_strip_whitespace=True,
        validate_assignment=True,
        extra="forbid",
    )


class ApiModel(AppModel):
    """Base for request/response models exposed over HTTP.

    Serializes as camelCase for JavaScript consumers while staying snake_case
    in Python. `populate_by_name` means both spellings are accepted on input.
    """

    model_config = ConfigDict(
        **AppModel.model_config,
        alias_generator=to_camel,
    )


class MongoModel(AppModel):
    """Base for documents persisted to MongoDB.

    Maps Mongo's `_id` to a Python `id` field and stamps created/updated
    timestamps. Stays snake_case: these fields are the stored shape, and
    renaming keys in the database to suit the frontend would be the wrong
    trade.
    """

    id: PyObjectId | None = Field(default=None, alias="_id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def to_mongo(self, *, exclude_none: bool = True) -> dict[str, Any]:
        """Serialize for insertion, dropping `_id` when unset so Mongo assigns one."""
        doc = self.model_dump(by_alias=True, exclude_none=exclude_none)
        if doc.get("_id") is None:
            doc.pop("_id", None)
        return doc


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class Page[T](ApiModel):
    """Cursor-friendly paginated envelope.

    Uses limit/offset for now because the UI needs jump-to-page. Swap for a
    keyset cursor if any collection outgrows ~50k rows per user.
    """

    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total
