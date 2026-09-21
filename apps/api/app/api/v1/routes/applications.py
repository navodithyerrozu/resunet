"""Application submission and tracking routes.

STUB: mock data plus TODOs.

Read this before implementing the submit path
---------------------------------------------
Submitting a job application is an **irreversible side effect on a third
party's system**. That single fact drives every design choice below:

* **Idempotency is mandatory, not a nicety.** A double-clicked button or a
  client retry after a timeout must not produce two applications at the same
  company. `Application.idempotency_key` is uniquely indexed, so the second
  insert fails at the database rather than relying on application-level
  checks that lose races.

* **Human-in-the-loop is the default.** `AUTOMATION_REQUIRE_APPROVAL=true`
  ships on. The worker fills the form, screenshots it, and pauses in
  `AWAITING_APPROVAL` until a person confirms. Full autopilot is available but
  opt-in, because the failure mode -- an incorrect application permanently
  associated with the user's name at a company they wanted -- is not
  recoverable.

* **Never retry blindly.** A submission that times out may well have
  succeeded. Retrying it risks a visible duplicate. Retries are gated on
  `AttemptOutcome.RETRYABLE_FAILURE`, which the worker only sets when it can
  establish the form was never submitted.

Further reliability and anti-bot concerns are documented in
`docs/SECURITY.md`.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, PaginationParams
from app.models.application import (
    Application,
    ApplicationCreate,
    ApplicationEvent,
    ApplicationStatus,
    ApplicationStatusUpdate,
    ApplicationSummary,
    AttemptOutcome,
    SubmissionAttempt,
    SubmissionMethod,
)
from app.models.base import Page, utcnow

router = APIRouter(prefix="/applications", tags=["applications"])

_MOCK_ID = "000000000000000000000000"


@router.post(
    "",
    response_model=Application,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit an application",
)
async def submit_application(payload: ApplicationCreate, user: CurrentUser) -> Application:
    """Create an application and queue the automated submission.

    202 because the submission has not happened yet -- the client polls or
    subscribes for the outcome.

    TODO, in this order:
      1. Verify the resume version exists, is owned by the user, and has a
         rendered `pdf_path`. There is nothing to upload otherwise.
      2. Verify `job.supports_automation` when method is AUTOMATED; fail early
         with a clear message rather than queueing work that cannot succeed.
      3. Insert the Application. Catch DuplicateKeyError on both
         `uq_user_job` and `uq_idempotency_key` and return the *existing*
         application with 200 rather than an error -- a retry should be a
         no-op, not a failure the client has to interpret.
      4. `enqueue_submission(...)` with jitter derived from how many
         submissions this user has fired at this board recently.
    """
    return Application(
        id=_MOCK_ID,
        user_id=str(user.id or _MOCK_ID),
        job_id=payload.job_id,
        resume_version_id=payload.resume_version_id,
        cover_letter_id=payload.cover_letter_id,
        status=ApplicationStatus.QUEUED,
        method=payload.method,
        arq_job_id="submit:000000000000000000000000",
        idempotency_key=payload.idempotency_key,
        answers=payload.answers,
    )


@router.get("", response_model=Page[ApplicationSummary], summary="List applications")
async def list_applications(
    user: CurrentUser,
    page: PaginationParams,
    status_filter: ApplicationStatus | None = Query(default=None, alias="status"),
) -> Page[ApplicationSummary]:
    """Tracker list, optionally filtered by pipeline stage.

    TODO: `ix_user_status_updated` covers this exactly. `$lookup` into `jobs`
    for company and title -- or denormalize both onto the application, since
    the tracker is the most-viewed screen in the product and a posting's
    company name never changes.
    """
    return Page[ApplicationSummary](
        items=[
            ApplicationSummary(
                id=_MOCK_ID,
                job_id=_MOCK_ID,
                company_name="Stripe",
                job_title="Senior Backend Engineer, Payments",
                status=ApplicationStatus.SUBMITTED,
                method=SubmissionMethod.AUTOMATED,
                attempt_count=1,
                submitted_at=utcnow(),
                follow_up_at=None,
                updated_at=utcnow(),
            )
        ],
        total=1,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{application_id}", response_model=Application, summary="Get an application")
async def get_application(application_id: str, user: CurrentUser) -> Application:
    """Full application including every submission attempt.

    The attempt list is the point of this endpoint: when something went wrong,
    the screenshot and final URL are what let a user decide whether to retry or
    apply manually.
    """
    return Application(
        id=application_id,
        user_id=str(user.id or _MOCK_ID),
        job_id=_MOCK_ID,
        resume_version_id=_MOCK_ID,
        status=ApplicationStatus.SUBMITTED,
        method=SubmissionMethod.AUTOMATED,
        attempts=[
            SubmissionAttempt(
                attempt_number=1,
                started_at=utcnow(),
                finished_at=utcnow(),
                outcome=AttemptOutcome.SUCCESS,
                final_url="https://boards.greenhouse.io/example/jobs/1234567/confirmation",
                confirmation_text="Thanks for applying! We'll be in touch.",
                screenshot_path="storage/traces/000000000000000000000000-final.png",
                duration_ms=42_300,
            )
        ],
        submitted_at=utcnow(),
        confirmation_id="GH-4451209",
        events=[
            ApplicationEvent(
                at=utcnow(),
                kind="status_change",
                summary="Submitted via Greenhouse automation.",
            )
        ],
    )


@router.patch(
    "/{application_id}/status",
    response_model=Application,
    summary="Update pipeline status",
)
async def update_status(
    application_id: str, payload: ApplicationStatusUpdate, user: CurrentUser
) -> Application:
    """Move an application through the pipeline (manual user action).

    TODO: append an `ApplicationEvent` on every transition rather than only
    setting `status`. The event timeline is what makes "how long does this
    company take to respond?" answerable later.
    """
    return Application(
        id=application_id,
        user_id=str(user.id or _MOCK_ID),
        job_id=_MOCK_ID,
        resume_version_id=_MOCK_ID,
        status=payload.status,
    )


@router.post(
    "/{application_id}/approve",
    response_model=Application,
    summary="Approve a paused submission",
)
async def approve_submission(application_id: str, user: CurrentUser) -> Application:
    """Confirm a form the worker filled and paused on.

    The human-in-the-loop gate. The worker has filled every field, captured a
    screenshot, and stopped short of the submit button.

    TODO: verify status is AWAITING_APPROVAL, then signal the worker to
    proceed. The browser context must still be alive, so the worker holds it
    open with a bounded timeout (~10 min) and marks the attempt
    `NEEDS_HUMAN` if approval never arrives. Do not attempt to resume a
    context that has already been torn down -- re-run the fill from scratch
    instead, because a half-restored session submits partial data.
    """
    return Application(
        id=application_id,
        user_id=str(user.id or _MOCK_ID),
        job_id=_MOCK_ID,
        resume_version_id=_MOCK_ID,
        status=ApplicationStatus.SUBMITTING,
    )


@router.post(
    "/{application_id}/retry",
    response_model=Application,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a failed submission",
)
async def retry_submission(application_id: str, user: CurrentUser) -> Application:
    """Re-queue a failed submission.

    TODO: refuse unless the last attempt's outcome is RETRYABLE_FAILURE, and
    refuse once `attempts >= settings.automation_max_attempts`.

    The refusal matters more than the retry. A PERMANENT_FAILURE means the
    posting closed or the form changed; retrying accomplishes nothing. A
    BLOCKED outcome means the site detected automation, and retrying makes
    that worse -- hammering after a bot wall is how an IP range gets banned
    for every user on the platform. Offer assisted mode instead.
    """
    return Application(
        id=application_id,
        user_id=str(user.id or _MOCK_ID),
        job_id=_MOCK_ID,
        resume_version_id=_MOCK_ID,
        status=ApplicationStatus.QUEUED,
    )


@router.get("/stats/funnel", summary="Application funnel stats")
async def funnel_stats(user: CurrentUser) -> dict[str, object]:
    """Aggregate counts per pipeline stage for the dashboard.

    TODO: a single `$group` on status, filtered by user_id.
    """
    return {
        "total": 47,
        "by_status": {
            "submitted": 31,
            "acknowledged": 12,
            "screening": 6,
            "interviewing": 3,
            "offer": 1,
            "rejected": 14,
            "ghosted": 8,
            "failed": 2,
        },
        "response_rate": 0.42,
        "interview_rate": 0.096,
        "median_days_to_response": 6,
    }
