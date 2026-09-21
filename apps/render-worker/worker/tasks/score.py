"""The ATS scoring task.

Contract with the API (`app/services/queue.py::enqueue_score`):
name `score_resume_ats`, kwargs `version_id`, `user_id`, `job_id`.

Runs on the render queue rather than its own, because it reads the compiled PDF
off the shared storage volume -- it has to execute where the artifact lives.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from worker import ats, db, storage

logger = structlog.get_logger(__name__)

ENGINE_VERSION = "v1"


async def score_resume_ats(
    ctx: dict[str, Any],
    *,
    version_id: str,
    user_id: str,
    job_id: str | None = None,
) -> dict[str, Any]:
    """Score a rendered resume version and append an `ats_scores` document."""
    log = logger.bind(version_id=version_id, user_id=user_id, job_id=job_id)

    version = await db.db()["resume_versions"].find_one(
        {"_id": _oid(version_id), "user_id": _oid(user_id)},
        {"pdf_path": 1, "page_count": 1},
    )
    if version is None:
        log.warning("ats.version_not_found")
        return {"ok": False, "error": "version not found"}
    if not version.get("pdf_path"):
        # Ordering guard. Scoring is chained after a successful render, but a
        # manual re-score or a retry can arrive before the PDF exists.
        log.info("ats.no_pdf_yet_skipping")
        return {"ok": False, "error": "resume has not been rendered yet"}

    pdf_bytes = await storage.read_pdf(version["pdf_path"])
    extraction = ats.extract_text(pdf_bytes)

    keywords = await _load_keywords(job_id) if job_id else None
    dimensions, findings, keyword_analysis = _assess(extraction, keywords)

    overall = sum(d["score"] * d["weight"] for d in dimensions)
    computed_at = datetime.now(UTC)

    await db.db()["ats_scores"].insert_one(
        {
            "user_id": _oid(user_id),
            "resume_version_id": _oid(version_id),
            "job_id": _oid(job_id) if job_id else None,
            "overall": round(overall, 1),
            "dimensions": dimensions,
            "findings": findings,
            "keyword_analysis": keyword_analysis,
            # Truncated to the model's declared ceiling. Storing the full text of
            # a ten-page PDF would bloat every document read in the score history.
            "extracted_text": extraction.text[:40_000],
            "extraction_confidence": extraction.confidence,
            "engine_version": ENGINE_VERSION,
            "computed_at": computed_at,
            "created_at": computed_at,
            "updated_at": computed_at,
        }
    )

    # Denormalized onto the resume so list views render a score without a join.
    await db.db()["resumes"].update_one(
        {"_id": version.get("resume_id"), "user_id": _oid(user_id)},
        {"$set": {"latest_ats_score": round(overall, 1), "updated_at": computed_at}},
    )

    log.info(
        "ats.scored",
        overall=round(overall, 1),
        confidence=extraction.confidence,
        findings=len(findings),
    )
    return {"ok": True, "overall": round(overall, 1), "findings": len(findings)}


def _assess(
    extraction: ats.ExtractionResult, keywords: dict[str, list[str]] | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
    """Compute every dimension and derive findings.

    Weights are renormalised when the keyword dimension is absent, so a
    format-only score is still out of 100 rather than silently capped at 75.
    """
    text = extraction.text
    dimensions: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    parse_score, parse_detail = ats.score_parseability(extraction)
    dimensions.append(_dim("parseability", parse_score, parse_detail))
    for problem in extraction.problems:
        findings.append(
            _finding(
                "parseability",
                "critical",
                "unparseable_content",
                problem,
                "Re-render with a supported template. If it persists, the source "
                "contains characters the font cannot represent.",
            )
        )

    struct_score, struct_detail = ats.score_section_structure(text)
    dimensions.append(_dim("section_structure", struct_score, struct_detail))
    if struct_score < 100:
        findings.append(
            _finding(
                "section_structure",
                "critical" if struct_score < 50 else "warning",
                "unrecognised_sections",
                struct_detail,
                "Use conventional headings -- 'Experience', 'Education', 'Skills'.",
            )
        )

    impact_score, impact_detail = ats.score_impact_quantification(text)
    dimensions.append(_dim("impact_quantification", impact_score, impact_detail))
    if impact_score < 80:
        findings.append(
            _finding(
                "impact_quantification",
                "warning",
                "unquantified_bullets",
                impact_detail,
                "Every bullet should carry a number. Re-run the resume agent on the "
                "entries flagged for review.",
                auto_fixable=True,
            )
        )

    contact_score, contact_detail = ats.score_contact_completeness(text)
    dimensions.append(_dim("contact_completeness", contact_score, contact_detail))
    if contact_score < 100:
        findings.append(
            _finding(
                "contact_completeness",
                "critical" if contact_score == 0 else "warning",
                "incomplete_contact",
                contact_detail,
                "Add the missing details to your profile's contact block.",
                auto_fixable=True,
            )
        )

    length_score, length_detail = ats.score_length_density(text, extraction.page_count)
    dimensions.append(_dim("length_density", length_score, length_detail))

    date_score, date_detail = ats.score_date_consistency(text)
    dimensions.append(_dim("date_consistency", date_score, date_detail))

    keyword_analysis: dict[str, Any] | None = None
    if keywords:
        kw_score, analysis = ats.score_keyword_match(
            text, required=keywords["required"], preferred=keywords["preferred"]
        )
        dimensions.append(
            _dim(
                "keyword_match",
                kw_score,
                f"{len(analysis['required_present'])} of "
                f"{len(keywords['required'])} required terms present.",
            )
        )
        keyword_analysis = analysis
        if analysis["required_missing"]:
            findings.append(
                _finding(
                    "keyword_match",
                    "critical",
                    "missing_required_keyword",
                    f"Missing required terms: {', '.join(analysis['required_missing'][:8])}.",
                    "Work these into a bullet only where you have real experience. "
                    "Claiming a skill you lack fails at the interview, not the filter.",
                )
            )
        if analysis["stuffing_warnings"]:
            findings.append(
                _finding(
                    "keyword_match",
                    "warning",
                    "keyword_stuffing",
                    f"Over-repeated: {', '.join(analysis['stuffing_warnings'])}.",
                    "Reduce to two or three mentions.",
                )
            )

    # Renormalise so the composite is always out of 100.
    total_weight = sum(d["weight"] for d in dimensions)
    if total_weight > 0:
        for d in dimensions:
            d["weight"] = round(d["weight"] / total_weight, 4)

    return dimensions, findings, keyword_analysis


async def _load_keywords(job_id: str) -> dict[str, list[str]] | None:
    doc = await db.db()["jobs"].find_one(
        {"_id": _oid(job_id)}, {"required_keywords": 1, "preferred_keywords": 1}
    )
    if doc is None:
        return None
    return {
        "required": doc.get("required_keywords", []),
        "preferred": doc.get("preferred_keywords", []),
    }


def _dim(name: str, score: float, detail: str) -> dict[str, Any]:
    return {
        "dimension": name,
        "score": score,
        "weight": ats.DIMENSION_WEIGHTS[name],
        "detail": detail,
    }


def _finding(
    dimension: str,
    severity: str,
    code: str,
    message: str,
    suggestion: str | None = None,
    *,
    auto_fixable: bool = False,
) -> dict[str, Any]:
    return {
        "dimension": dimension,
        "severity": severity,
        "code": code,
        "message": message,
        "suggestion": suggestion,
        "targetBulletId": None,
        "autoFixable": auto_fixable,
    }


def _oid(value: str) -> Any:  # noqa: ANN401
    from bson import ObjectId

    return ObjectId(value)


__all__ = ["ENGINE_VERSION", "score_resume_ats"]
