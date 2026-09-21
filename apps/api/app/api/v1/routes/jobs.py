"""Job ingest and matching routes.

STUB: returns realistic mock data with TODOs for the real implementation.

Matching is a two-stage design. A cheap deterministic pass (keyword overlap,
seniority, location) filters and scores everything; an optional embedding
pass adds semantic similarity for the top candidates only. Running embeddings
over every posting a user has ever seen is the obvious approach and the wrong
one -- it costs real money per job and adds little above keyword overlap for
the clearly-bad matches.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, PaginationParams
from app.models.base import Page, utcnow
from app.models.job import (
    Compensation,
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

router = APIRouter(prefix="/jobs", tags=["jobs"])

_MOCK_ID = "000000000000000000000000"


@router.post(
    "",
    response_model=Job,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a job posting",
)
async def ingest_job(payload: JobIngestRequest, user: CurrentUser) -> Job:
    """Ingest by URL or pasted description.

    TODO:
      1. If a URL is given, derive `(board, external_id)` from it and upsert on
         that key -- ten users saving the same posting must produce one document
         and one parse.
      2. Enqueue `PARSE_JOB_POSTING` for keyword and requirement extraction.
      3. Enqueue `COMPUTE_JOB_MATCH` for the requesting user.

    On scraping: prefer the board's public JSON API where one exists
    (Greenhouse and Lever both expose one) over driving a browser. It is
    faster, it does not trip bot detection, and it is unambiguously permitted.
    Fall back to pasted text rather than to scraping -- asking the user to
    paste always works and never gets you blocked.
    """
    return Job(
        id=_MOCK_ID,
        source=JobSource(
            board=JobBoard.GREENHOUSE,
            url=payload.url or "https://boards.greenhouse.io/example/jobs/1234567",
            external_id="1234567",
            scraped_at=utcnow(),
        ),
        company_name=payload.company_name or "Stripe",
        company_domain="stripe.com",
        title=payload.title or "Senior Backend Engineer, Payments",
        location="Remote (US)",
        remote_type=RemoteType.REMOTE,
        compensation=Compensation(min_amount=180_000, max_amount=250_000, equity=True),
        description_raw=payload.description_raw or "We are looking for a backend engineer...",
        parsed=ParsedJob(
            responsibilities=[
                "Design and operate high-throughput payment services",
                "Improve reliability of the charge pipeline",
            ],
            requirements=[
                "5+ years backend engineering",
                "Strong Python or Go",
                "Experience with distributed systems",
            ],
            preferred=["Payments domain experience", "Kubernetes"],
            keywords=[
                KeywordWeight(term="Python", weight=0.95, required=True, occurrences=4),
                KeywordWeight(term="distributed systems", weight=0.88, required=True,
                              occurrences=3),
                KeywordWeight(term="Kubernetes", weight=0.62, required=False, occurrences=2),
            ],
            seniority=Seniority.SENIOR,
            min_years_experience=5,
        ),
        status=JobStatus.OPEN,
        supports_automation=True,
        posted_at=utcnow(),
    )


@router.get("", response_model=Page[JobSummary], summary="List saved jobs")
async def list_jobs(
    user: CurrentUser,
    page: PaginationParams,
    min_score: float | None = Query(default=None, ge=0, le=100,
                                    description="Filter by minimum match score."),
    status_filter: JobStatus | None = Query(default=None, alias="status"),
) -> Page[JobSummary]:
    """Saved jobs with the requesting user's match score joined in.

    TODO: aggregate from `job_matches` (indexed on `(user_id, score DESC)`)
    with a `$lookup` into `jobs`. Drive from matches rather than from jobs:
    `jobs` is a shared collection, and starting there means scanning postings
    that belong to other users' searches.
    """
    return Page[JobSummary](
        items=[
            JobSummary(
                id=_MOCK_ID,
                company_name="Stripe",
                title="Senior Backend Engineer, Payments",
                location="Remote (US)",
                remote_type=RemoteType.REMOTE,
                status=JobStatus.OPEN,
                supports_automation=True,
                posted_at=utcnow(),
                match_score=91.2,
            )
        ],
        total=1,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{job_id}", response_model=Job, summary="Get a job posting")
async def get_job(job_id: str, user: CurrentUser) -> Job:
    """Full posting including the structured parse.

    TODO: `jobs` is shared, so there is no user_id to filter on. Authorise via
    the existence of a `job_matches` or `applications` row linking this user to
    this job -- otherwise the endpoint is an enumeration oracle over every
    posting in the database.
    """
    return await ingest_job(JobIngestRequest(description_raw="stub"), user)


@router.post(
    "/{job_id}/match",
    response_model=JobMatch,
    summary="Compute this user's match against a job",
)
async def compute_match(job_id: str, user: CurrentUser) -> JobMatch:
    """Score the user's profile against one posting.

    TODO: two-stage as described in the module docstring. Deterministic
    components first; add `semantic_similarity` from cached embeddings only
    when the cheap score clears a threshold.

    `missing_keywords` is the highest-value output here -- it is the direct
    input to the resume agent's tailoring pass, so populate it carefully and
    keep the terms verbatim from the posting.
    """
    return JobMatch(
        id=_MOCK_ID,
        user_id=str(user.id or _MOCK_ID),
        job_id=job_id,
        score=91.2,
        components=MatchComponents(
            skill_overlap=94.0,
            keyword_coverage=88.0,
            seniority_fit=100.0,
            semantic_similarity=89.5,
            location_fit=100.0,
        ),
        matched_keywords=["Python", "distributed systems", "Redis", "payments", "AWS"],
        missing_keywords=["Kubernetes", "Terraform"],
        rationale=(
            "Strong overlap on backend and payments experience; seniority aligns with the "
            "5+ year requirement. Two infrastructure keywords are absent, both of which "
            "your raw notes suggest you have unstated experience with."
        ),
        computed_at=utcnow(),
    )


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

    TODO: read `job_matches` sorted by score DESC, then filter to open
    postings. Exclude jobs the user has already applied to -- seeing a role
    you applied to last week at the top of "recommended for you" reads as a
    bug even when the score is correct.
    """
    return [
        JobSummary(
            id=_MOCK_ID,
            company_name="Stripe",
            title="Senior Backend Engineer, Payments",
            location="Remote (US)",
            remote_type=RemoteType.REMOTE,
            status=JobStatus.OPEN,
            supports_automation=True,
            posted_at=utcnow(),
            match_score=91.2,
        )
    ]
