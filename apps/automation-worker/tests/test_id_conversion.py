"""Tests for the worker's Mongo id handling.

The API stores ids as BSON ObjectIds while the queue hands the worker hex
strings. A query or ownership check that does not bridge that boundary
silently misses existing documents -- the failure this module exists to
prevent. These tests use only the pure helpers, no database.
"""

from __future__ import annotations

from bson import ObjectId

from worker.tasks.submit import _id_candidates, _id_filter, _same_owner, _user_filter


def test_hex_string_yields_both_forms() -> None:
    oid = ObjectId()
    candidates = _id_candidates(str(oid))
    assert oid in candidates
    assert str(oid) in candidates


def test_object_id_yields_both_forms() -> None:
    oid = ObjectId()
    candidates = _id_candidates(oid)
    assert oid in candidates
    assert str(oid) in candidates


def test_non_hex_string_is_not_crashed_on() -> None:
    # Legacy string ids must still be queryable; converting blindly with
    # ObjectId() would raise InvalidId here.
    assert _id_candidates("legacy-string-id") == ["legacy-string-id"]
    assert _id_filter("legacy-string-id") == {"_id": {"$in": ["legacy-string-id"]}}


def test_none_yields_no_candidates() -> None:
    assert _id_candidates(None) == []
    assert _user_filter(None) == {"user_id": {"$in": []}}


def test_owner_comparison_bridges_object_id_and_string() -> None:
    oid = ObjectId()
    assert _same_owner(oid, str(oid))
    assert _same_owner(str(oid), str(oid))
    assert not _same_owner(oid, str(ObjectId()))
    assert not _same_owner(None, str(oid))
    assert not _same_owner(oid, None)
