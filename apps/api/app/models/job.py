"""Job postings and per-user match scores.

Two collections, because postings are shared and matches are personal.

`jobs` is deduplicated globally on `(source.board, source.external_id)`. Ten
users watching the same Stripe listing store one posting, one parse, one
embedding -- the parse is the expensive part, so paying for it once matters.

`job_matches` holds the per-user score. Keeping it out of `jobs` avoids an
unbounded per-user array inside a shared document (which would also make the
posting a write-contention hotspot).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, HttpUrl, model_validator

from app.models.base import ApiModel, AppModel, MongoModel, PyObjectId


class JobBoard(StrEnum):
    """Boards we can parse and, where marked, submit to.

    Submission support is per-board because every ATS renders its form
    differently -- see `apps/automation-worker/worker/adapters`.
    """

    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    WORKDAY = "workday"
    ASHBY = "ashby"
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    COMPANY_SITE = "company_site"
    MANUAL = "manual"


class RemoteType(StrEnum):
    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class Seniority(StrEnum):
    INTERN = "intern"
    ENTRY = "entry"
    MID = "mid"
    SENIOR = "senior"
    STAFF = "staff"
    PRINCIPAL = "principal"
    MANAGER = "manager"
    UNKNOWN = "unknown"


class JobStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


class JobSource(AppModel):
    board: JobBoard
    url: HttpUrl
    external_id: str | None = Field(
        default=None,
        description="Board-native posting id. Combined with `board` this forms the "
        "dedupe key, so prefer it over URL matching -- URLs carry tracking params.",
    )
    scraped_at: datetime | None = None


class Compensation(AppModel):
    min_amount: float | None = Field(default=None, ge=0)
    max_amount: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    period: str = Field(default="year", description="'year', 'month', or 'hour'.")
    equity: bool = False

    @model_validator(mode="after")
    def _check_range(self) -> Compensation:
        if self.min_amount and self.max_amount and self.max_amount < self.min_amount:
            raise ValueError("max compensation below min")
        return self


class KeywordWeight(AppModel):
    """A skill or term extracted from the posting, with importance.

    `required` versus nice-to-have drives ATS scoring: missing a required
    keyword is a hard penalty, missing a preferred one is a soft nudge.
    """

    term: str = Field(min_length=1, max_length=80)
    weight: float = Field(ge=0, le=1, description="Extracted importance, 0-1.")
    required: bool = False
    occurrences: int = Field(default=1, ge=1)


class ParsedJob(AppModel):
    """Structured extraction from the raw posting text.

    Computed once at ingest. Deliberately stored rather than derived on read:
    it costs an LLM call, and job descriptions are immutable in practice.
    """

    responsibilities: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    preferred: list[str] = Field(default_factory=list)
    keywords: list[KeywordWeight] = Field(default_factory=list)
    seniority: Seniority = Seniority.UNKNOWN
    min_years_experience: float | None = Field(default=None, ge=0, le=50)
    parser_version: str = Field(
        default="v1",
        description="Bump when extraction logic changes so stale parses can be "
        "backfilled selectively instead of wholesale.",
    )


class Job(MongoModel):
    """Collection: `jobs`. Shared across users."""

    source: JobSource
    company_name: str = Field(min_length=1, max_length=200)
    company_domain: str | None = None
    title: str = Field(min_length=1, max_length=250)
    location: str | None = None
    remote_type: RemoteType = RemoteType.UNKNOWN
    compensation: Compensation | None = None

    description_raw: str = Field(
        max_length=60_000, description="Original posting text; the parse input and audit trail."
    )
    parsed: ParsedJob | None = None
    embedding: list[float] | None = Field(
        default=None, description="Posting embedding for semantic matching."
    )

    status: JobStatus = JobStatus.OPEN
    posted_at: datetime | None = None
    closes_at: datetime | None = None
    last_seen_at: datetime | None = Field(
        default=None, description="Last time a crawl confirmed the posting is live."
    )

    supports_automation: bool = Field(
        default=False,
        description="Whether an automation adapter exists for this board. Gates the "
        "'Auto-apply' affordance in the UI so we never offer what we cannot do.",
    )


class MatchComponents(AppModel):
    """Score breakdown. Surfaced verbatim in the UI.

    An opaque "73% match" is not actionable; naming which dimension dragged
    the score down is what makes a user change their resume.
    """

    skill_overlap: float = Field(ge=0, le=100)
    keyword_coverage: float = Field(ge=0, le=100)
    seniority_fit: float = Field(ge=0, le=100)
    semantic_similarity: float = Field(ge=0, le=100)
    location_fit: float = Field(ge=0, le=100)


class JobMatch(MongoModel):
    """Collection: `job_matches`. Unique on `(user_id, job_id)`."""

    user_id: PyObjectId
    job_id: PyObjectId
    score: float = Field(ge=0, le=100)
    components: MatchComponents
    matched_keywords: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(
        default_factory=list,
        description="Required terms absent from the profile. Directly seeds the "
        "resume agent's tailoring pass.",
    )
    rationale: str | None = Field(default=None, max_length=1500)
    engine_version: str = Field(default="v1")
    computed_at: datetime


# --- API contracts ---------------------------------------------------------


class JobIngestRequest(ApiModel):
    """Ingest by URL or by pasted text.

    Pasted text is the pragmatic path: many boards block scrapers, and asking
    the user to paste a description always works.
    """

    url: HttpUrl | None = None
    description_raw: str | None = Field(default=None, max_length=60_000)
    company_name: str | None = None
    title: str | None = None

    @model_validator(mode="after")
    def _require_one(self) -> JobIngestRequest:
        if not self.url and not self.description_raw:
            raise ValueError("provide either `url` or `description_raw`")
        return self


class JobSummary(ApiModel):
    id: PyObjectId
    company_name: str
    title: str
    location: str | None
    remote_type: RemoteType
    status: JobStatus
    supports_automation: bool
    posted_at: datetime | None
    match_score: float | None = Field(
        default=None, description="Joined from `job_matches` for the requesting user."
    )
