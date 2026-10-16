"""Job applications and the automation attempts behind them.

Two things worth flagging here, both security-relevant.

**Credentials are never stored on the application.** Job-board logins live
in a separate `board_credentials` collection holding only Fernet-encrypted
ciphertext, and the decryption key is issued *only* to the automation
worker. The API process cannot decrypt them even if fully compromised. See
`docs/ARCHITECTURE.md` -> "Credential isolation".

**Every automation attempt is recorded, including failures.** Submitting a
job application is an irreversible side effect on someone else's system.
When a run fails halfway you must be able to answer "did it actually
submit?" -- so attempts carry a screenshot, a trace path, and the terminal
URL. This is also the audit trail if a board ever asks what you were doing.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, HttpUrl

from app.models.base import ApiModel, AppModel, MongoModel, PyObjectId


class ApplicationStatus(StrEnum):
    """Pipeline stage. Ordered roughly by funnel progression."""

    DRAFT = "draft"
    QUEUED = "queued"                    # handed to the automation worker
    SUBMITTING = "submitting"
    AWAITING_APPROVAL = "awaiting_approval"  # form filled, paused for human confirm
    SUBMITTED = "submitted"
    FAILED = "failed"
    BLOCKED = "blocked"  # automation worker encountered a bot wall or access barrier
    NEEDS_REVIEW = "needs_review"  # worker stopped safely on an unmapped or ambiguous form
    ACKNOWLEDGED = "acknowledged"
    SCREENING = "screening"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    GHOSTED = "ghosted"


class SubmissionMethod(StrEnum):
    AUTOMATED = "automated"
    MANUAL = "manual"              # user applied themselves, tracked here
    ASSISTED = "assisted"          # bot filled the form, human clicked submit


class AttemptOutcome(StrEnum):
    SUCCESS = "success"
    RETRYABLE_FAILURE = "retryable_failure"    # timeout, transient nav error
    PERMANENT_FAILURE = "permanent_failure"    # posting closed, form changed
    BLOCKED = "blocked"                        # captcha / bot wall hit
    NEEDS_HUMAN = "needs_human"                # unmapped required field


class BlockReason(StrEnum):
    """Why an automated run was stopped by the target site.

    Tracked as an enum rather than a free-text error because these drive
    real operational decisions: a spike in CAPTCHA on one board means back
    off that board, not retry harder.
    """

    CAPTCHA = "captcha"
    CLOUDFLARE = "cloudflare"
    RATE_LIMITED = "rate_limited"
    LOGIN_REQUIRED = "login_required"
    MFA_REQUIRED = "mfa_required"
    IP_BLOCKED = "ip_blocked"
    UNKNOWN = "unknown"


class FieldMapping(AppModel):
    """One resolved form field, retained for debugging and replay.

    Values are redacted for anything sensitive -- this document is read by
    support tooling.
    """

    selector: str
    label: str | None = None
    value_preview: str = Field(
        max_length=120, description="Truncated/redacted value actually entered."
    )
    strategy: str = Field(
        description="How the field was resolved: 'label_match', 'heuristic', 'llm_fallback'."
    )
    confidence: float = Field(ge=0, le=1)


class SubmissionAttempt(AppModel):
    """One automation run. Embedded -- attempts are bounded and always read
    together with their application."""

    attempt_number: int = Field(ge=1)
    started_at: datetime
    finished_at: datetime | None = None
    outcome: AttemptOutcome | None = None
    block_reason: BlockReason | None = None

    error_message: str | None = None
    failed_step: str | None = Field(
        default=None, description="Named pipeline step that failed, e.g. 'upload_resume'."
    )
    final_url: HttpUrl | None = Field(
        default=None,
        description="URL when the run ended. A confirmation page here is the strongest "
        "available evidence the submission actually landed.",
    )
    confirmation_text: str | None = Field(
        default=None, max_length=1000, description="Scraped confirmation message, if any."
    )

    screenshot_path: str | None = None
    trace_path: str | None = Field(
        default=None,
        description="Playwright trace zip. Records DOM snapshots and network for "
        "post-mortem replay -- captured on failure only, since traces are large.",
    )
    field_mappings: list[FieldMapping] = Field(default_factory=list)
    duration_ms: int | None = Field(default=None, ge=0)


class ApplicationEvent(AppModel):
    """Timeline entry: status changes, emails, interview invites."""

    at: datetime
    kind: str = Field(description="e.g. 'status_change', 'email_received', 'note'.")
    summary: str = Field(max_length=500)
    metadata: dict[str, str] = Field(default_factory=dict)


class Application(MongoModel):
    """Collection: `applications`. Unique on `(user_id, job_id)`."""

    user_id: PyObjectId
    job_id: PyObjectId
    resume_version_id: PyObjectId = Field(
        description="The exact immutable version submitted. Never a mutable resume id -- "
        "editing a resume later must not rewrite what was sent."
    )
    cover_letter_id: PyObjectId | None = None

    status: ApplicationStatus = ApplicationStatus.DRAFT
    method: SubmissionMethod = SubmissionMethod.AUTOMATED

    attempts: list[SubmissionAttempt] = Field(default_factory=list)
    arq_job_id: str | None = None
    idempotency_key: str | None = Field(
        default=None,
        description="Client-supplied key, uniquely indexed. Prevents a double-clicked "
        "submit button from firing two real applications at a company.",
    )

    submitted_at: datetime | None = None
    confirmation_id: str | None = Field(
        default=None, description="Reference number returned by the board, if any."
    )
    events: list[ApplicationEvent] = Field(default_factory=list)

    answers: dict[str, str] = Field(
        default_factory=dict,
        description="Answers to board-specific screening questions, keyed by question hash "
        "so repeat questions across postings can be reused.",
    )
    notes: str | None = Field(default=None, max_length=4000)
    follow_up_at: datetime | None = None

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    @property
    def is_terminal(self) -> bool:
        """Whether the application has reached a state no retry can change."""
        return self.status in {
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.REJECTED,
            ApplicationStatus.WITHDRAWN,
            ApplicationStatus.OFFER,
            ApplicationStatus.GHOSTED,
        }


class BoardCredential(MongoModel):
    """Collection: `board_credentials`.

    SECURITY: this document holds ciphertext only. `encrypted_blob` is a
    Fernet token over a JSON payload of {username, password, ...}. The
    decryption key lives in the automation worker's environment and is
    explicitly blanked in the API service (see docker-compose.yml), so the
    API can list and delete credentials but never read them.

    Consider migrating to a real KMS/secrets manager before production --
    Fernet with an env-var key is a reasonable local-dev posture, not a
    long-term one, because key rotation and per-user isolation are manual.
    """

    user_id: PyObjectId
    board: str = Field(description="JobBoard value this credential authenticates against.")
    username_hint: str = Field(
        max_length=80,
        description="Masked identifier for the UI, e.g. 'a***@gmail.com'. Never the "
        "full username -- this is the only field the API is permitted to display.",
    )
    encrypted_blob: str = Field(description="Fernet ciphertext. Opaque to the API service.")
    key_version: int = Field(default=1, ge=1, description="Supports key rotation.")

    last_used_at: datetime | None = None
    last_verified_at: datetime | None = None
    is_valid: bool = Field(
        default=True,
        description="Flipped false when a login attempt fails, so the worker stops "
        "retrying with credentials known to be dead (which looks like a brute-force "
        "attempt to the target site).",
    )


# --- API contracts ---------------------------------------------------------


class ApplicationCreate(ApiModel):
    job_id: PyObjectId
    resume_version_id: PyObjectId
    cover_letter_id: PyObjectId | None = None
    method: SubmissionMethod = SubmissionMethod.AUTOMATED
    answers: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str | None = Field(
        default=None, max_length=100,
        description="Strongly recommended. Generate client-side per submit intent.",
    )


class ApplicationStatusUpdate(ApiModel):
    status: ApplicationStatus
    note: str | None = Field(default=None, max_length=500)


class ApplicationSummary(ApiModel):
    """Tracker list-view projection. Omits attempts, which are large."""

    id: PyObjectId
    job_id: PyObjectId
    company_name: str
    job_title: str
    status: ApplicationStatus
    method: SubmissionMethod
    attempt_count: int
    submitted_at: datetime | None
    follow_up_at: datetime | None
    updated_at: datetime
