from __future__ import annotations

from app.db.mongo import INDEX_PLAN, Collections
from app.models.ats import ATSScore


def test_ats_score_idempotency_index_is_unique_but_ignores_legacy_scores() -> None:
    index = next(
        model.document
        for model in INDEX_PLAN[Collections.ATS_SCORES]
        if model.document["name"] == "uq_ats_idempotency_key"
    )

    assert index["key"] == {"idempotency_key": 1}
    assert index["unique"] is True
    assert index["partialFilterExpression"] == {
        "idempotency_key": {"$type": "string"}
    }
    assert "idempotency_key" not in ATSScore.model_json_schema(mode="serialization")["properties"]
