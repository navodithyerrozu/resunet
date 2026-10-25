"""Job ingest and matching routes.

Real MongoDB-backed implementations, deterministic throughout.

Matching is the two-stage design from the original docstring, with only
stage one implemented: a cheap deterministic pass (keyword overlap,
seniority, location) scores everything. The embedding stage is deliberately
NOT run -- there is no embedding pipeline in this service, and fabricating a
`semantic_similarity` number would be worse than reporting the keyword
score honestly. `semantic_similarity` is therefore set to the keyword
coverage and the rationale says so, until embeddings exist.

On scraping: this module never fetches a board URL. Ingest by URL only
derives `(board, external_id)` from the URL string and stores the user's
own pasted description (or, for URL-only ingest, an empty description to
be filled by pasting). Boards' public JSON APIs and pasted text are the
permitted paths; driving a browser at a board to scrape it is not done here.

`PARSE_JOB_POSTING` / `COMPUTE_JOB_MATCH` are in
`app.services.queue.UNIMPLEMENTED_TASKS` -- enqueueing them would lose the
job silently, so parsing happens synchronously at ingest and matching
happens synchronously in `/match` and at ingest (so an ingested job
immediately appears in the user's own list).
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

from bson import ObjectId
from fastapi import APIRouter, Query, Response, status

from app.api.deps import CurrentUser, PaginationParams
from app.core.errors import NotFoundError
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
from app.models.user import User

router = APIRouter(prefix="/jobs", tags=["jobs"])

# Terms a deterministic extractor can recognise without an LLM. This is a
# vocabulary list, not user data: a term only counts as matched when it
# actually appears in the posting text and in the user's profile.
_KNOWN_TERMS: tuple[str, ...] = (
    "python", "java", "javascript", "typescript", "go", "golang", "rust", "c++", "c#",
    "react", "next.js", "node.js", "node", "fastapi", "django", "flask", "express",
    "kubernetes", "docker", "aws", "gcp", "azure", "terraform", "ansible",
    "sql", "postgresql", "postgres", "mysql", "mongodb", "redis", "kafka", "graphql",
    "rest", "grpc", "ci/cd", "git", "linux", "pandas", "numpy", "scikit-learn",
    "pytorch", "tensorflow", "machine learning", "deep learning", "llm",
    "langchain", "langgraph", "playwright", "selenium", "spark", "airflow",
    "elasticsearch", "nginx", "microservices", "distributed systems",
    "system design", "data structures", "algorithms", "devops", "figma",
    "html", "css", "tailwind", "vue", "angular", "swift", "kotlin", "dart",
    "flutter", "android", "ios", "embedded", "esp32", "ble", "iot",
)

_SENIORITY_WORDS: tuple[tuple[str, Seniority], ...] = (
    ("principal", Seniority.PRINCIPAL),
    ("staff", Seniority.STAFF),
    ("senior", Seniority.SENIOR),
    ("manager", Seniority.MANAGER),
    ("intern", Seniority.INTERN),
    ("entry", Seniority.ENTRY),
    ("junior", Seniority.ENTRY),
    ("new grad", Seniority.ENTRY),
    ("mid", Seniority.MID),
)

_SENIORITY_YEARS: dict[Seniority, float] = {
    Seniority.INTERN: 0,
    Seniority.ENTRY: 1,
    Seniority.MID: 3,
    Seniority.SENIOR: 5,
    Seniority.STAFF: 8,
    Seniority.PRINCIPAL: 10,
    Seniority.MANAGER: 6,
    Seniority.UNKNOWN: 3,
}


def _oid(user: User) -> ObjectId:
    return ObjectId(str(user.id))


def _user_filter(user: User) -> dict[str, object]:
    oid = _oid(user)
    return {"user_id": {"$in": [oid, str(oid)]}}


def _mongo_doc(model: object) -> dict[str, object]:
    """Serialize a MongoModel for insertion, with ObjectId foreign keys."""
    doc = model.to_mongo()  # type: ignore[attr-defined]
    for field in ("user_id", "job_id", "resume_version_id"):
        value = doc.get(field)
        if isinstance(value, str) and ObjectId.is_valid(value):
            doc[field] = ObjectId(value)
    doc.pop("_id", None)
    return doc


def _derive_board(url: str) -> tuple[JobBoard, str | None]:
    """Derive (board, external_id) from a URL string. No network access.

    The external id is the board-native posting id from the path, which is
    what the global dedupe key uses -- URLs carry tracking params, ids do
    not. Returns `external_id=None` when the URL carries no usable id, in
    which case the posting is stored as a one-off (no dedupe key).
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.strip("/")
    segments = [s for s in path.split("/") if s]
    last = segments[-1] if segments else None
    if "greenhouse" in host:
        external = None
        if "jobs" in segments:
            idx = segments.index("jobs")
            external = segments[idx + 1] if idx + 1 < len(segments) else None
        return JobBoard.GREENHOUSE, external
    if "lever" in host:
        return JobBoard.LEVER, last
    if "myworkdayjobs" in host or "workday" in host:
        return JobBoard.WORKDAY, last
    if "ashbyhq" in host:
        return JobBoard.ASHBY, last
    if "linkedin" in host:
        match = re.search(r"/jobs/view/(\d+)", parsed.path)
        return JobBoard.LINKEDIN, match.group(1) if match else None
    if "indeed" in host:
        match = re.search(r"[?&]jk=([A-Za-z0-9]+)", parsed.query)
        return JobBoard.INDEED, match.group(1) if match else None
    return JobBoard.COMPANY_SITE, None


def _word_present(term: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


def _extract_keywords(text: str) -> list[KeywordWeight]:
    """Deterministic keyword extraction from posting text.

    Required vs preferred comes from section headings: terms in a
    "requirements" block are required, terms in a "preferred / nice to have"
    block are preferred, everything else is unweighted context. Occurrences
    are literal counts in the text.
    """
    lowered = text.lower()
    required_block = ""
    preferred_block = ""
    req_match = re.search(
        r"requirements?.{0,20}\n(.*?)(?:\n\s*(?:preferred|nice to have|bonus|qualifications))",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if req_match:
        required_block = req_match.group(1)
    pref_match = re.search(
        r"(?:preferred|nice to have|bonus).{0,20}\n(.*)", text, re.IGNORECASE | re.DOTALL
    )
    if pref_match:
        preferred_block = pref_match.group(1)
    keywords: list[KeywordWeight] = []
    for term in _KNOWN_TERMS:
        if not _word_present(term, lowered):
            continue
        occurrences = len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", lowered))
        required = _word_present(term, required_block) if required_block else False
        preferred = _word_present(term, preferred_block) if preferred_block else False
        weight = 0.9 if required else (0.6 if preferred else 0.5)
        keywords.append(
            KeywordWeight(term=term, weight=weight, required=required, occurrences=occurrences)
        )
    return keywords


def _extract_parsed(text: str) -> ParsedJob:
    lowered = text.lower()
    seniority = Seniority.UNKNOWN
    for word, level in _SENIORITY_WORDS:
        if _word_present(word, lowered):
            seniority = level
            break
    years_match = re.search(r"(\d+(?:\.\d+)?)\+?\s*years?", lowered)
    min_years = float(years_match.group(1)) if years_match else None
    if min_years is not None and min_years > 50:
        min_years = 50.0
    bullet_lines = [
        line.strip(" -*•\t")
        for line in text.splitlines()
        if line.strip().startswith(("-", "*", "•"))
    ]
    location = None
    loc_match = re.search(r"location\s*:\s*(.+)", text, re.IGNORECASE)
    if loc_match:
        location = loc_match.group(1).strip()[:200]
    remote_type = RemoteType.UNKNOWN
    if _word_present("remote", lowered):
        remote_type = RemoteType.REMOTE
    elif _word_present("hybrid", lowered):
        remote_type = RemoteType.HYBRID
    elif _word_present("on-site", lowered) or _word_present("onsite", lowered):
        remote_type = RemoteType.ONSITE
    return ParsedJob(
        responsibilities=bullet_lines[:20],
        requirements=bullet_lines[:20],
        preferred=[],
        keywords=_extract_keywords(text),
        seniority=seniority,
        min_years_experience=min_years,
    ), remote_type, location


async def _load_profile(user: User) -> CareerProfile | None:
    doc = await collection(Collections.CAREER_PROFILES).find_one(_user_filter(user))
    return CareerProfile.model_validate(doc) if doc is not None else None


def _profile_corpus(
    profile: CareerProfile | None, bullet_texts: list[str]
) -> tuple[set[str], float, str | None]:
    """(lowercased skill/term corpus, years of experience, location)."""
    terms: set[str] = set()
    years = 0.0
    location: str | None = None
    if profile is not None:
        terms.update(s.name.lower() for s in profile.skills)
        if profile.contact.location:
            location = profile.contact.location
        today = date.today()
        for entry in profile.experiences:
            terms.update(t.lower() for t in entry.tech_stack)
            end = entry.dates.end or today
            years += max(0.0, (end - entry.dates.start).days / 365.25)
        for project in profile.projects:
            terms.update(t.lower() for t in project.tech_stack)
    for text in bullet_texts:
        terms.update(t.lower() for t in _KNOWN_TERMS if _word_present(t, text))
    return terms, years, location


def _score_match(
    *, job: Job, profile: CareerProfile | None, bullet_texts: list[str]
) -> JobMatch:
    """Deterministic stage-one score. No embeddings, no invented data."""
    parsed = job.parsed
    terms = parsed.keywords if parsed is not None else []
    corpus, years, location = _profile_corpus(profile, bullet_texts)
    matched: list[str] = []
    missing: list[str] = []
    weighted_total = 0.0
    weighted_matched = 0.0
    for keyword in terms:
        weighted_total += keyword.weight
        if keyword.term.lower() in corpus or _word_present(
            keyword.term, " ".join(bullet_texts)
        ):
            matched.append(keyword.term)
            weighted_matched += keyword.weight
        elif keyword.required:
            missing.append(keyword.term)
    if terms:
        skill_overlap = round(len(matched) / len(terms) * 100, 2)
        keyword_coverage = (
            round(weighted_matched / weighted_total * 100, 2) if weighted_total else 50.0
        )
    else:
        # No keywords to compare: neutral, not flattering.
        skill_overlap = 50.0
        keyword_coverage = 50.0
    required_years = (
        parsed.min_years_experience
        if parsed is not None and parsed.min_years_experience is not None
        else _SENIORITY_YEARS.get(parsed.seniority if parsed else Seniority.UNKNOWN, 3)
    )
    if parsed is None or parsed.seniority is Seniority.UNKNOWN:
        seniority_fit = 50.0
    elif required_years <= 0:
        seniority_fit = 100.0
    elif years >= required_years:
        seniority_fit = 70.0 if years > required_years + 8 else 100.0
    else:
        seniority_fit = round(years / required_years * 100, 2)
    if job.remote_type is RemoteType.REMOTE:
        location_fit = 100.0
    elif not job.location or not location:
        location_fit = 50.0
    elif location.lower() in job.location.lower() or job.location.lower() in location.lower():
        location_fit = 100.0
    elif job.remote_type is RemoteType.HYBRID:
        location_fit = 70.0
    else:
        location_fit = 30.0
    # No embedding stage exists; report keyword coverage here and say so in
    # the rationale, rather than inventing a separate semantic number.
    semantic = keyword_coverage
    score = round(
        skill_overlap * 0.25
        + keyword_coverage * 0.30
        + seniority_fit * 0.20
        + location_fit * 0.15
        + semantic * 0.10,
        2,
    )
    rationale = (
        f"Matched {len(matched)} of {len(terms)} posting terms; "
        f"{len(missing)} required term(s) missing"
        + (f": {', '.join(missing)}." if missing else ".")
        + f" Seniority fit {seniority_fit:.0f}%, location fit {location_fit:.0f}%. "
        "Semantic similarity is not computed separately (no embeddings); "
        "it mirrors keyword coverage."
    )
    return JobMatch(
        user_id="",
        job_id=str(job.id),
        score=score,
        components=MatchComponents(
            skill_overlap=skill_overlap,
            keyword_coverage=keyword_coverage,
            seniority_fit=seniority_fit,
            semantic_similarity=semantic,
            location_fit=location_fit,
        ),
        matched_keywords=matched,
        missing_keywords=missing,
        rationale=rationale,
        engine_version="v1-deterministic",
        computed_at=utcnow(),
    )


async def _compute_and_store_match(job: Job, user: User) -> JobMatch:
    """Compute a match from the user's profile and upsert it for this user."""
    profile = await _load_profile(user)
    bullet_docs = await (
        collection(Collections.BULLETS)
        .find({**_user_filter(user), "status": {"$ne": "archived"}})
        .to_list(length=500)
    )
    bullet_texts = []
    for doc in bullet_docs:
        parts = [doc.get("action", ""), doc.get("metric", ""), doc.get("method", "")]
        bullet_texts.append(" ".join(p for p in parts if p))
    match = _score_match(job=job, profile=profile, bullet_texts=bullet_texts)
    match.user_id = str(user.id)
    doc = _mongo_doc(match)
    await collection(Collections.JOB_MATCHES).update_one(
        {"user_id": _oid(user), "job_id": ObjectId(str(job.id))},
        {"$set": doc, "$setOnInsert": {"user_id": _oid(user), "job_id": ObjectId(str(job.id))}},
        upsert=True,
    )
    stored = await collection(Collections.JOB_MATCHES).find_one(
        {"user_id": _oid(user), "job_id": ObjectId(str(job.id))}
    )
    if stored is None:  # pragma: no cover - upsert just succeeded
        raise NotFoundError()
    return JobMatch.model_validate(stored)


def _summary_from_job(job: Job, match_score: float | None) -> JobSummary:
    return JobSummary(
        id=str(job.id),
        company_name=job.company_name,
        title=job.title,
        location=job.location,
        remote_type=job.remote_type,
        status=job.status,
        supports_automation=job.supports_automation,
        posted_at=job.posted_at,
        match_score=match_score,
    )


async def _job_by_id(job_id: str) -> Job:
    if not ObjectId.is_valid(job_id):
        raise NotFoundError()
    doc = await collection(Collections.JOBS).find_one({"_id": ObjectId(job_id)})
    if doc is None:
        raise NotFoundError()
    return Job.model_validate(doc)


@router.post(
    "",
    response_model=Job,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a job posting",
)
async def ingest_job(
    payload: JobIngestRequest, user: CurrentUser, response: Response
) -> Job:
    """Ingest by URL or pasted description, deduplicated on board+external_id.

    Ten users saving the same posting produce one document and one parse:
    when the dedupe key already exists, only `last_seen_at` is refreshed and
    the existing posting is returned (with 200, not a second 201). A fresh
    match row is computed for the requesting user either way, so the posting
    immediately appears in their own list -- which is also what authorises
    `GET /jobs/{id}` for them without turning that endpoint into an
    enumeration oracle over shared postings.
    """
    description = payload.description_raw or ""
    board = JobBoard.MANUAL
    external_id: str | None = None
    url_str: str | None = None
    if payload.url is not None:
        url_str = str(payload.url)
        board, external_id = _derive_board(url_str)
    if external_id is not None:
        existing = await collection(Collections.JOBS).find_one(
            {"source.board": board.value, "source.external_id": external_id}
        )
        if existing is not None:
            await collection(Collections.JOBS).update_one(
                {"_id": existing["_id"]}, {"$set": {"last_seen_at": utcnow()}}
            )
            job = Job.model_validate(existing)
            response.status_code = status.HTTP_200_OK
            await _compute_and_store_match(job, user)
            return job

    parsed, remote_type, parsed_location = _extract_parsed(description)
    first_line = description.splitlines()[0].strip()[:250] if description.strip() else ""
    title = payload.title or first_line or "Untitled posting"
    company_name = payload.company_name
    if company_name is None and payload.url is not None:
        host = urlparse(str(payload.url)).hostname or ""
        # Greenhouse/Lever URLs carry the company slug in the first path segment.
        segments = [s for s in urlparse(str(payload.url)).path.strip("/").split("/") if s]
        company_name = segments[0].replace("-", " ").title() if segments and board in (
            JobBoard.GREENHOUSE, JobBoard.LEVER,
        ) else host
    job = Job(
        source=JobSource(
            board=board,
            url=url_str or "https://example.invalid/manual",
            external_id=external_id,
            scraped_at=utcnow(),
        ),
        company_name=company_name or "Unknown company",
        company_domain=None,
        title=title,
        location=parsed_location,
        remote_type=remote_type,
        description_raw=description,
        parsed=parsed,
        status=JobStatus.OPEN,
        supports_automation=board in (JobBoard.GREENHOUSE, JobBoard.LEVER),
        last_seen_at=utcnow(),
    )
    doc = job.to_mongo()
    # JobSource.url is an HttpUrl; store it as a plain string for Mongo.
    doc["source"]["url"] = url_str or "https://example.invalid/manual"
    result = await collection(Collections.JOBS).insert_one(doc)
    doc["_id"] = result.inserted_id
    job = Job.model_validate(doc)
    await _compute_and_store_match(job, user)
    return job


@router.get("", response_model=Page[JobSummary], summary="List saved jobs")
async def list_jobs(
    user: CurrentUser,
    page: PaginationParams,
    min_score: float | None = Query(default=None, ge=0, le=100,
                                    description="Filter by minimum match score."),
    status_filter: JobStatus | None = Query(default=None, alias="status"),
) -> Page[JobSummary]:
    """Saved jobs, driven from the user's `job_matches`, not from shared `jobs`.

    Starting from `jobs` would scan postings belonging to other users'
    searches. Matches are indexed on `(user_id, score DESC)`; the join to the
    posting is a point lookup per row, and filtering happens after the join
    so a score/status filter cannot silently drop a page boundary.
    """
    match_docs = await (
        collection(Collections.JOB_MATCHES)
        .find(_user_filter(user), sort=[("score", -1)])
        .to_list(length=5000)
    )
    summaries: list[JobSummary] = []
    for match_doc in match_docs:
        if min_score is not None and float(match_doc.get("score", 0)) < min_score:
            continue
        job_doc = await collection(Collections.JOBS).find_one(
            {"_id": match_doc["job_id"]}
        )
        if job_doc is None:
            continue
        job = Job.model_validate(job_doc)
        if status_filter is not None and job.status is not status_filter:
            continue
        summaries.append(_summary_from_job(job, float(match_doc.get("score", 0))))
    window = summaries[page.offset : page.offset + page.limit]
    return Page[JobSummary](
        items=window,
        total=len(summaries),
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{job_id}", response_model=Job, summary="Get a job posting")
async def get_job(job_id: str, user: CurrentUser) -> Job:
    """Full posting including the structured parse.

    `jobs` is shared, so there is no user_id to filter on. Authorisation is
    via the existence of a `job_matches` or `applications` row linking this
    user to this job -- otherwise the endpoint is an enumeration oracle over
    every posting in the database.
    """
    job = await _job_by_id(job_id)
    job_oid = ObjectId(job_id)
    match = await collection(Collections.JOB_MATCHES).find_one(
        {"user_id": _oid(user), "job_id": job_oid}
    )
    if match is None:
        application = await collection(Collections.APPLICATIONS).find_one(
            {"user_id": _oid(user), "job_id": job_oid}
        )
        if application is None:
            raise NotFoundError()
    return job


@router.post(
    "/{job_id}/match",
    response_model=JobMatch,
    summary="Compute this user's match against a job",
)
async def compute_match(job_id: str, user: CurrentUser) -> JobMatch:
    """Score the user's profile against one posting (deterministic stage one).

    `missing_keywords` is the highest-value output here -- it is the direct
    input to the resume agent's tailoring pass, so terms are kept verbatim
    from the posting.
    """
    job = await _job_by_id(job_id)
    if job.parsed is None and job.description_raw:
        parsed, _, _ = _extract_parsed(job.description_raw)
        await collection(Collections.JOBS).update_one(
            {"_id": ObjectId(job_id)}, {"$set": {"parsed": parsed.model_dump(mode="json")}}
        )
        job = Job.model_validate({**job.model_dump(), "parsed": parsed})
    return await _compute_and_store_match(job, user)


@router.get(
    "/recommendations/top",
    response_model=list[JobSummary],
    summary="Top job recommendations",
)
async def top_recommendations(
    user: CurrentUser,
    limit: int = Query(default=10, ge=1, le=50),
) -> list[JobSummary]:
    """Highest-scoring open postings for the dashboard.

    Jobs the user has already applied to are excluded -- seeing a role you
    applied to last week at the top of "recommended for you" reads as a bug
    even when the score is correct.
    """
    match_docs = await (
        collection(Collections.JOB_MATCHES)
        .find(_user_filter(user), sort=[("score", -1)])
        .to_list(length=5000)
    )
    applied = await (
        collection(Collections.APPLICATIONS)
        .find(_user_filter(user))
        .to_list(length=5000)
    )
    applied_job_ids = {str(doc.get("job_id")) for doc in applied}
    summaries: list[JobSummary] = []
    for match_doc in match_docs:
        if str(match_doc.get("job_id")) in applied_job_ids:
            continue
        job_doc = await collection(Collections.JOBS).find_one(
            {"_id": match_doc["job_id"]}
        )
        if job_doc is None:
            continue
        job = Job.model_validate(job_doc)
        if job.status is not JobStatus.OPEN:
            continue
        summaries.append(_summary_from_job(job, float(match_doc.get("score", 0))))
        if len(summaries) >= limit:
            break
    return summaries
