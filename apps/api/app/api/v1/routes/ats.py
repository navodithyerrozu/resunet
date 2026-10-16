"""ATS scoring routes backed by compiled-PDF scoring worker results."""

from __future__ import annotations

from bson import ObjectId
from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, PaginationParams, fetch_owned
from app.core.errors import ConflictError, NotFoundError, QueueUnavailableError
from app.db.mongo import Collections, collection
from app.models.ats import ATSScore, ScoreComparison, ScoreRequest, ScoreSubmission
from app.models.base import Page
from app.services.queue import enqueue_score

router = APIRouter(prefix="/ats", tags=["ats"])


async def _require_job_access(
    job_id: str, user: CurrentUser, *, version_doc: dict[str, object] | None = None
) -> None:
    """A shared job is visible only after the caller saved or applied to it."""
    if not ObjectId.is_valid(job_id):
        raise NotFoundError()
    job_oid = ObjectId(job_id)
    user_oid = ObjectId(str(user.id))
    match = await collection(Collections.JOB_MATCHES).find_one(
        {"job_id": job_oid, "user_id": user_oid}
    )
    if match is None:
        match = await collection(Collections.APPLICATIONS).find_one(
            {"job_id": job_oid, "user_id": user_oid}
        )
    if match is None and version_doc is not None:
        resume_id = version_doc.get("resume_id")
        if isinstance(resume_id, ObjectId):
            match = await collection(Collections.RESUMES).find_one(
                {
                    "_id": resume_id,
                    "user_id": user_oid,
                    "target_job_id": job_oid,
                }
            )
    if match is None:
        raise NotFoundError()


@router.post(
    "/score",
    response_model=ScoreSubmission,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue ATS scoring for a rendered resume version",
)
async def score_resume(payload: ScoreRequest, user: CurrentUser) -> ScoreSubmission:
    """Queue scoring against the PDF artifact; results are read from score history."""
    version_id = str(payload.resume_version_id)
    version_doc = await fetch_owned(Collections.RESUME_VERSIONS, version_id, user)
    if not version_doc.get("pdf_path"):
        raise ConflictError("This resume version has not finished rendering.")
    if payload.job_id is not None:
        await _require_job_access(str(payload.job_id), user, version_doc=version_doc)

    try:
        arq_job_id = await enqueue_score(
            version_id=version_id,
            user_id=str(user.id),
            job_id=str(payload.job_id) if payload.job_id is not None else None,
        )
    except Exception as exc:  # noqa: BLE001 - the request must not claim it was queued
        raise QueueUnavailableError() from exc
    return ScoreSubmission(resume_version_id=version_id, arq_job_id=arq_job_id)


@router.get(
    "/scores/{version_id}",
    response_model=Page[ATSScore],
    response_model_by_alias=False,
    summary="Score history for a version",
)
async def score_history(
    version_id: str,
    user: CurrentUser,
    page: PaginationParams,
    job_id: str | None = Query(default=None, alias="jobId"),
    include_extracted_text: bool = Query(default=False, alias="includeExtractedText"),
) -> Page[ATSScore]:
    """List persisted worker scores; large extracted text is opt-in."""
    version_doc = await fetch_owned(Collections.RESUME_VERSIONS, version_id, user)
    query: dict[str, object] = {
        "resume_version_id": ObjectId(version_id),
        "user_id": ObjectId(str(user.id)),
    }
    if job_id is not None:
        await _require_job_access(job_id, user, version_doc=version_doc)
        query["job_id"] = ObjectId(job_id)
    scores = collection(Collections.ATS_SCORES)
    projection = None if include_extracted_text else {"extracted_text": 0}
    docs = await (
        scores.find(query, projection)
        .sort("computed_at", -1)
        .skip(page.offset)
        .limit(page.limit)
        .to_list(length=page.limit)
    )
    return Page[ATSScore](
        items=[ATSScore.model_validate(doc) for doc in docs],
        total=await scores.count_documents(query),
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/compare",
    response_model=ScoreComparison,
    summary="Compare two versions",
)
async def compare_versions(
    user: CurrentUser,
    baseline_version_id: str = Query(description="The earlier version."),
    candidate_version_id: str = Query(description="The version to evaluate."),
) -> ScoreComparison:
    """Compare newest persisted scores and diff stable finding codes."""
    await fetch_owned(Collections.RESUME_VERSIONS, baseline_version_id, user)
    await fetch_owned(Collections.RESUME_VERSIONS, candidate_version_id, user)
    scores = collection(Collections.ATS_SCORES)
    user_oid = ObjectId(str(user.id))
    baseline = await scores.find_one(
        {"resume_version_id": ObjectId(baseline_version_id), "user_id": user_oid},
        sort=[("computed_at", -1)],
    )
    candidate = await scores.find_one(
        {"resume_version_id": ObjectId(candidate_version_id), "user_id": user_oid},
        sort=[("computed_at", -1)],
    )
    if baseline is None or candidate is None:
        raise NotFoundError("Both versions must have a completed ATS score.")
    old_codes = {finding["code"] for finding in baseline.get("findings", [])}
    new_codes = {finding["code"] for finding in candidate.get("findings", [])}
    return ScoreComparison(
        baseline_version_id=baseline_version_id,
        candidate_version_id=candidate_version_id,
        baseline_overall=baseline["overall"],
        candidate_overall=candidate["overall"],
        resolved_findings=sorted(old_codes - new_codes),
        new_findings=sorted(new_codes - old_codes),
    )


@router.get(
    "/extracted-text/{version_id}",
    summary="What an ATS actually sees",
)
async def extracted_text(version_id: str, user: CurrentUser) -> dict[str, object]:
    """Return the most recent PDF text extraction for an owned version."""
    await fetch_owned(Collections.RESUME_VERSIONS, version_id, user)
    score = await collection(Collections.ATS_SCORES).find_one(
        {
            "resume_version_id": ObjectId(version_id),
            "user_id": ObjectId(str(user.id)),
        },
        sort=[("computed_at", -1)],
        projection={"extracted_text": 1, "extraction_confidence": 1},
    )
    if score is None:
        raise NotFoundError("No completed ATS extraction exists for this version.")
    confidence = score.get("extraction_confidence")
    warnings = (
        ["The PDF text extraction was incomplete; review the compiled document carefully."]
        if confidence is not None and confidence < 0.8
        else []
    )
    return {
        "version_id": version_id,
        "extraction_confidence": confidence,
        "text": score.get("extracted_text") or "",
        "warnings": warnings,
    }
