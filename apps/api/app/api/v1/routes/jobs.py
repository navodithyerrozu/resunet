"""Persistent user-submitted job postings and deterministic match scoring.

Ingestion accepts pasted job text only. URL fetching, embeddings, and external
boards remain unconfigured, so those values are never invented or implied.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Query, status
from pymongo.errors import DuplicateKeyError

from app.api.deps import CurrentUser, PaginationParams
from app.core.errors import ConflictError, NotFoundError
from app.db.mongo import Collections, collection
from app.models.base import Page, utcnow
from app.models.job import (
    Job,
    JobBoard,
    JobIngestRequest,
    JobMatch,
    JobSource,
    JobStatus,
    JobSummary,
    KeywordWeight,
    MatchComponents,
    ParsedJob,
    RemoteType,
    Seniority,
)
from app.models.profile import CareerProfile

router = APIRouter(prefix="/jobs", tags=["jobs"])

_KEYWORD_CATALOG = (
    "Python", "JavaScript", "TypeScript", "Java", "Go", "C++", "C#", "Rust", "SQL",
    "PostgreSQL", "MongoDB", "Redis", "Kafka", "AWS", "Azure", "GCP", "Docker",
    "Kubernetes", "Terraform", "React", "Node.js", "FastAPI", "Django", "Spring",
    "GraphQL", "REST", "CI/CD", "machine learning", "distributed systems",
)
_REQUIRED_RE = re.compile(r"\b(required|must have|minimum|need to have)\b", re.I)
_PREFERRED_RE = re.compile(r"\b(preferred|nice to have|bonus|ideally)\b", re.I)
_YEARS_RE = re.compile(r"\b(\d+(?:\.\d+)?)\+?\s+years?\b", re.I)


def _bson_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, bytes, ObjectId, datetime)):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _bson_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bson_safe(item) for item in value]
    return str(value)


def _job_parse(description: str, title: str) -> ParsedJob:
    """Extract conservative structured fields from words in the supplied text."""
    lines = [re.sub(r"^\s*[-*•\d.)]+\s*", "", row).strip() for row in description.splitlines()]
    lines = [row for row in lines if row]
    requirements = [line for line in lines if _REQUIRED_RE.search(line)]
    preferred = [line for line in lines if _PREFERRED_RE.search(line)]
    responsibilities = [
        line for line in lines
        if re.match(
            r"^(design|build|develop|lead|own|operate|manage|deliver|improve)\b",
            line,
            re.I,
        )
    ]

    keyword_rows: list[KeywordWeight] = []
    for term in _KEYWORD_CATALOG:
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.I)
        occurrences = len(pattern.findall(description))
        if not occurrences:
            continue
        required = any(pattern.search(line) for line in requirements)
        is_preferred = any(pattern.search(line) for line in preferred)
        keyword_rows.append(
            KeywordWeight(
                term=term,
                weight=1.0 if required else 0.5 if is_preferred else 0.75,
                required=required,
                occurrences=occurrences,
            )
        )

    lowered = title.casefold()
    seniority = next(
        (
            level
            for words, level in (
                (("principal",), Seniority.PRINCIPAL),
                (("staff",), Seniority.STAFF),
                (("manager", "director", "head of"), Seniority.MANAGER),
                (("senior", "sr."), Seniority.SENIOR),
                (("mid", "intermediate"), Seniority.MID),
                (("junior", "entry", "associate"), Seniority.ENTRY),
                (("intern",), Seniority.INTERN),
            )
            if any(word in lowered for word in words)
        ),
        Seniority.UNKNOWN,
    )
    years = [float(value) for value in _YEARS_RE.findall(description)]
    return ParsedJob(
        responsibilities=responsibilities[:30],
        requirements=requirements[:50],
        preferred=preferred[:30],
        keywords=keyword_rows,
        seniority=seniority,
        min_years_experience=max(years) if years else None,
        parser_version="heuristic-v1",
    )


def _profile_terms(profile: CareerProfile) -> set[str]:
    values = [skill.name for skill in profile.skills]
    values.extend(tag for entry in profile.experiences for tag in entry.tech_stack)
    values.extend(tag for entry in profile.projects for tag in entry.tech_stack)
    return {value.strip().casefold() for value in values if value.strip()}


def _experience_years(profile: CareerProfile) -> float:
    today = date.today()
    days = 0
    for entry in profile.experiences:
        end = entry.dates.end or today
        days += max(0, (end - entry.dates.start).days)
    return round(days / 365.25, 1)


def _seniority_fit(profile: CareerProfile, parsed: ParsedJob) -> float | None:
    if parsed.seniority is Seniority.UNKNOWN and parsed.min_years_experience is None:
        return None
    years = _experience_years(profile)
    if parsed.min_years_experience is not None:
        return min(100.0, round(years / max(parsed.min_years_experience, 1.0) * 100, 1))
    profile_level = (
        Seniority.INTERN if years < 1 else
        Seniority.ENTRY if years < 3 else
        Seniority.MID if years < 6 else
        Seniority.SENIOR
    )
    levels = [Seniority.INTERN, Seniority.ENTRY, Seniority.MID, Seniority.SENIOR,
              Seniority.STAFF, Seniority.PRINCIPAL]
    if parsed.seniority is Seniority.MANAGER:
        return None
    distance = abs(levels.index(profile_level) - levels.index(parsed.seniority))
    return max(0.0, 100.0 - 25.0 * distance)


def _match_job(job: Job, profile: CareerProfile) -> JobMatch:
    parsed = job.parsed or ParsedJob()
    profile_terms = _profile_terms(profile)
    matches = [keyword for keyword in parsed.keywords if keyword.term.casefold() in profile_terms]
    total_weight = sum(keyword.weight for keyword in parsed.keywords)
    keyword_coverage = (
        round(sum(keyword.weight for keyword in matches) / total_weight * 100, 1)
        if total_weight else None
    )
    skill_overlap = (
        round(len(matches) / len(parsed.keywords) * 100, 1) if parsed.keywords else None
    )
    seniority_fit = _seniority_fit(profile, parsed)
    dimensions = [
        (keyword_coverage, 0.55),
        (skill_overlap, 0.25),
        (seniority_fit, 0.20),
    ]
    available = [(value, weight) for value, weight in dimensions if value is not None]
    score = (
        round(
            sum(value * weight for value, weight in available)
            / sum(weight for _, weight in available),
            1,
        )
        if available
        else None
    )
    matched = [keyword.term for keyword in matches]
    missing_required = [
        keyword.term
        for keyword in parsed.keywords
        if keyword.required and keyword.term.casefold() not in profile_terms
    ]
    rationale = (
        f"Heuristic v1: {len(matched)} of {len(parsed.keywords)} extracted job keywords "
        "match profile skills or experience tags. Location is not assessed because no user "
        "preference is stored; semantic similarity is not computed because no embedding model "
        "is configured."
    )
    return JobMatch(
        user_id=str(profile.user_id),
        job_id=str(job.id),
        score=score,
        components=MatchComponents(
            skill_overlap=skill_overlap,
            keyword_coverage=keyword_coverage,
            seniority_fit=seniority_fit,
            semantic_similarity=None,
            location_fit=None,
        ),
        matched_keywords=matched,
        missing_keywords=missing_required,
        rationale=rationale,
        engine_version="heuristic-v1",
        computed_at=utcnow(),
    )


async def _save_match(job: Job, profile: CareerProfile) -> JobMatch:
    match = _match_job(job, profile)
    user_oid = ObjectId(str(profile.user_id))
    job_oid = ObjectId(str(job.id))
    doc = _bson_safe(match.model_dump(by_alias=True, exclude_none=True))
    doc.pop("_id", None)
    doc["user_id"] = user_oid
    doc["job_id"] = job_oid
    await collection(Collections.JOB_MATCHES).update_one(
        {"user_id": user_oid, "job_id": job_oid},
        {"$set": doc},
        upsert=True,
    )
    stored = await collection(Collections.JOB_MATCHES).find_one(
        {"user_id": user_oid, "job_id": job_oid}
    )
    if stored is None:
        raise RuntimeError("Job match write was not persisted")
    return JobMatch.model_validate(stored)


def _summary(row: dict[str, Any]) -> JobSummary:
    job = row.get("job", row)
    return JobSummary(
        id=job["_id"],
        company_name=job["company_name"],
        title=job["title"],
        location=job.get("location"),
        remote_type=job.get("remote_type", RemoteType.UNKNOWN.value),
        status=job.get("status", JobStatus.UNKNOWN.value),
        supports_automation=bool(job.get("supports_automation", False)),
        posted_at=job.get("posted_at"),
        match_score=row.get("match_score"),
    )


def _job_from_doc(doc: dict[str, Any]) -> Job:
    """Hide the internal deduplication key from the public Job contract."""
    public_doc = {key: value for key, value in doc.items() if key != "dedupe_hash"}
    return Job.model_validate(public_doc)


def _job_list_pipeline(
    *,
    user_id: ObjectId,
    offset: int,
    limit: int,
    min_score: float | None = None,
    status_filter: JobStatus | None = None,
    exclude_applied: bool = False,
) -> list[dict[str, Any]]:
    score_filter: dict[str, Any] = {"user_id": user_id}
    if min_score is not None:
        score_filter["score"] = {"$gte": min_score}
    pipeline: list[dict[str, Any]] = [
        {"$match": score_filter},
        {
            "$lookup": {
                "from": Collections.JOBS.value,
                "localField": "job_id",
                "foreignField": "_id",
                "as": "job",
            }
        },
        {"$unwind": "$job"},
    ]
    if exclude_applied:
        pipeline.extend(
            [
                {
                    "$lookup": {
                        "from": Collections.APPLICATIONS.value,
                        "let": {"job_id": "$job._id"},
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$job_id", "$$job_id"]},
                                            {"$eq": ["$user_id", user_id]},
                                        ]
                                    }
                                }
                            },
                            {"$limit": 1},
                        ],
                        "as": "user_application",
                    }
                },
                {"$match": {"user_application.0": {"$exists": False}}},
            ]
        )
    job_filter: dict[str, Any] = {}
    if status_filter is not None:
        job_filter["job.status"] = status_filter.value
    if job_filter:
        pipeline.append({"$match": job_filter})
    pipeline.extend(
        [
            {"$sort": {"score": -1, "computed_at": -1}},
            {
                "$facet": {
                    "items": [
                        {"$skip": offset},
                        {"$limit": limit},
                        {"$project": {"job": 1, "match_score": "$score", "_id": 0}},
                    ],
                    "total": [{"$count": "value"}],
                }
            },
        ]
    )
    return pipeline


@router.post(
    "",
    response_model=Job,
    response_model_by_alias=False,
    status_code=status.HTTP_201_CREATED,
    summary="Save a pasted job posting",
)
async def ingest_job(payload: JobIngestRequest, user: CurrentUser) -> Job:
    """Store user-supplied posting data and compute a profile match."""
    description = payload.description_raw.strip() if payload.description_raw else ""
    title = payload.title.strip() if payload.title else ""
    company = payload.company_name.strip() if payload.company_name else ""
    dedupe_text = re.sub(r"\s+", " ", f"{company}\n{title}\n{description}").casefold()
    dedupe_hash = hashlib.sha256(dedupe_text.encode()).hexdigest()
    jobs = collection(Collections.JOBS)
    stored = await jobs.find_one({"dedupe_hash": dedupe_hash})
    profile_doc = await collection(Collections.CAREER_PROFILES).find_one(
        {"user_id": ObjectId(str(user.id))}
    )
    if profile_doc is None:
        raise NotFoundError("Career profile not found.")
    profile = CareerProfile.model_validate(profile_doc)

    if stored is None:
        job = Job(
            source=JobSource(board=JobBoard.MANUAL, url=payload.url),
            company_name=company,
            title=title,
            description_raw=description,
            parsed=_job_parse(description, title),
            status=payload.status,
            supports_automation=False,
        )
        doc = _bson_safe(job.model_dump(by_alias=True, exclude_none=True))
        doc.pop("_id", None)
        doc["dedupe_hash"] = dedupe_hash
        try:
            inserted = await jobs.insert_one(doc)
            job = job.model_copy(update={"id": str(inserted.inserted_id)})
        except DuplicateKeyError:
            stored = await jobs.find_one({"dedupe_hash": dedupe_hash})
            if stored is None:
                raise ConflictError(
                    "A matching job was added concurrently; retry the request."
                ) from None
            job = _job_from_doc(stored)
    else:
        job = _job_from_doc(stored)

    await _save_match(job, profile)
    return job


@router.get("", response_model=Page[JobSummary], summary="List saved jobs")
async def list_jobs(
    user: CurrentUser,
    page: PaginationParams,
    min_score: float | None = Query(default=None, ge=0, le=100),
    status_filter: JobStatus | None = Query(default=None, alias="status"),
) -> Page[JobSummary]:
    """List only postings linked to this user by a persisted match."""
    pipeline = _job_list_pipeline(
        user_id=ObjectId(str(user.id)),
        offset=page.offset,
        limit=page.limit,
        min_score=min_score,
        status_filter=status_filter,
    )
    result = await collection(Collections.JOB_MATCHES).aggregate(pipeline).to_list(length=1)
    facets = result[0] if result else {"items": [], "total": []}
    total_rows = facets.get("total", [])
    total = int(total_rows[0]["value"]) if total_rows else 0
    return Page[JobSummary](
        items=[_summary(row) for row in facets.get("items", [])],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


async def _require_job_access(job_id: str, user: CurrentUser) -> dict[str, Any]:
    if not ObjectId.is_valid(job_id):
        raise NotFoundError()
    job_oid = ObjectId(job_id)
    user_oid = ObjectId(str(user.id))
    match = await collection(Collections.JOB_MATCHES).find_one(
        {"user_id": user_oid, "job_id": job_oid}
    )
    application = await collection(Collections.APPLICATIONS).find_one(
        {"user_id": user_oid, "job_id": job_oid}, {"_id": 1}
    )
    if match is None and application is None:
        raise NotFoundError()
    job = await collection(Collections.JOBS).find_one({"_id": job_oid})
    if job is None:
        raise NotFoundError()
    return job


@router.get(
    "/{job_id}",
    response_model=Job,
    response_model_by_alias=False,
    summary="Get an accessible job posting",
)
async def get_job(job_id: str, user: CurrentUser) -> Job:
    return _job_from_doc(await _require_job_access(job_id, user))


@router.post(
    "/{job_id}/match",
    response_model=JobMatch,
    response_model_by_alias=False,
    summary="Recompute this user's profile match",
)
async def compute_match(job_id: str, user: CurrentUser) -> JobMatch:
    job = _job_from_doc(await _require_job_access(job_id, user))
    profile_doc = await collection(Collections.CAREER_PROFILES).find_one(
        {"user_id": ObjectId(str(user.id))}
    )
    if profile_doc is None:
        raise NotFoundError("Career profile not found.")
    return await _save_match(job, CareerProfile.model_validate(profile_doc))


@router.get(
    "/recommendations/top",
    response_model=list[JobSummary],
    summary="Top open job recommendations",
)
async def top_recommendations(
    user: CurrentUser,
    limit: int = Query(default=10, ge=1, le=50),
) -> list[JobSummary]:
    user_oid = ObjectId(str(user.id))
    pipeline = _job_list_pipeline(
        user_id=user_oid,
        offset=0,
        limit=limit,
        status_filter=JobStatus.OPEN,
        exclude_applied=True,
    )
    facets = await collection(Collections.JOB_MATCHES).aggregate(pipeline).to_list(length=1)
    rows = facets[0].get("items", []) if facets else []
    return [_summary(row) for row in rows]
