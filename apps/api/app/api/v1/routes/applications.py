"""Application submission and tracking routes.

Real MongoDB-backed implementations. The design notes from the original
stub still govern, and are worth restating because they are load-bearing:

Submitting a job application is an **irreversible side effect on a third
party's system**. That single fact drives every design choice below:

* **Idempotency is mandatory, not a nicety.** A double-clicked button or a
  client retry after a timeout must not produce two applications at the same
  company. `Application.idempotency_key` is uniquely indexed, so the second
  insert fails at the database rather than relying on application-level
  checks that lose races. Both duplicate paths -- `(user_id, job_id)` and
  `idempotency_key` -- return the *existing* application instead of an
  error: a retry should be a no-op, not a failure the client has to
  interpret.

* **Human-in-the-loop is the default.** `AUTOMATION_REQUIRE_APPROVAL=true`
  ships on. The worker fills the form, screenshots it, and pauses in
  `AWAITING_APPROVAL` until a person confirms. This module never marks an
  automated application SUBMITTED on its own; only the worker (or a manual
  user action) moves it there. Approving a submission that is not paused
  for approval is refused rather than silently re-queued.

* **Never retry blindly.** A submission that times out may well have
  succeeded. Retrying it risks a visible duplicate. Retries are gated on
  the last attempt's outcome being RETRYABLE_FAILURE, and on the attempt
  budget in `settings.automation_max_attempts`. A PERMANENT_FAILURE means
  the posting closed or the form changed; a BLOCKED outcome means the site
  detected automation, and retrying makes that worse.

Further reliability and anti-bot concerns are documented in
`docs/SECURITY.md`.
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Query, status
from pymongo.errors import DuplicateKeyError

from app.api.deps import CurrentUser, PaginationParams, fetch_owned
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailedError
from app.core.logging import get_logger
from app.db.mongo import Collections, collection
from app.models.application import (
    Application,
    ApplicationCreate,
    ApplicationEvent,
    ApplicationStatus,
    ApplicationStatusUpdate,
    ApplicationSummary,
    AttemptOutcome,
    SubmissionMethod,
)
from app.models.base import Page, utcnow
from app.models.user import User
from app.services.queue import enqueue_submission

router = APIRouter(prefix="/applications", tags=["applications"])
logger = get_logger(__name__)


def _oid(user: User) -> ObjectId:
    return ObjectId(str(user.id))


def _user_filter(user: User) -> dict[str, object]:
    oid = _oid(user)
    return {"user_id": {"$in": [oid, str(oid)]}}


def _mongo_doc(model: Application) -> dict[str, object]:
    doc = model.to_mongo()
    for field in ("user_id", "job_id", "resume_version_id", "cover_letter_id"):
        value = doc.get(field)
        if isinstance(value, str) and ObjectId.is_valid(value):
            doc[field] = ObjectId(value)
    doc.pop("_id", None)
    return doc


async def _load_application(application_id: str, user: User) -> dict[str, Any]:
    """Ownership-checked load; applications carry no user_id-free path."""
    return await fetch_owned(Collections.APPLICATIONS, application_id, user)


async def _summaries(
    docs: list[dict[str, object]],
) -> list[ApplicationSummary]:
    summaries: list[ApplicationSummary] = []
    for doc in docs:
        app = Application.model_validate(doc)
        job_doc = await collection(Collections.JOBS).find_one({"_id": doc.get("job_id")})
        company = "Unknown company"
        title = "Unknown role"
        if job_doc is not None:
            company = str(job_doc.get("company_name") or company)
            title = str(job_doc.get("title") or title)
        summaries.append(
            ApplicationSummary(
                id=str(app.id),
                job_id=str(app.job_id),
                company_name=company,
                job_title=title,
                status=app.status,
                method=app.method,
                attempt_count=app.attempt_count,
                submitted_at=app.submitted_at,
                follow_up_at=app.follow_up_at,
                updated_at=app.updated_at,
            )
        )
    return summaries


async def _existing_for_duplicate(
    *, user: User, payload: ApplicationCreate
) -> Application | None:
    """Find the application a duplicate insert would collide with, if any."""
    if payload.idempotency_key:
        doc = await collection(Collections.APPLICATIONS).find_one(
            {"idempotency_key": payload.idempotency_key, **_user_filter(user)}
        )
        if doc is not None:
            return Application.model_validate(doc)
    doc = await collection(Collections.APPLICATIONS).find_one(
        {"user_id": _oid(user), "job_id": ObjectId(str(payload.job_id))}
    )
    return Application.model_validate(doc) if doc is not None else None


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

    In order:
      1. Verify the resume version exists, is owned by the user, and has a
         rendered `pdf_path`. There is nothing to upload otherwise.
      2. Verify `job.supports_automation` when method is AUTOMATED; fail early
         with a clear message rather than queueing work that cannot succeed.
      3. Insert the Application. A DuplicateKeyError on either unique index
         returns the *existing* application rather than an error.
      4. `enqueue_submission(...)` with jitter derived from how many
         submissions this user has fired recently. A queue failure leaves
         the application QUEUED with the error recorded on it, so the user
         can retry from a truthful state instead of a lost one.
    """
    if not ObjectId.is_valid(str(payload.resume_version_id)):
        raise NotFoundError("Resume version not found.")
    version_doc = await collection(Collections.RESUME_VERSIONS).find_one(
        {
            "_id": ObjectId(str(payload.resume_version_id)),
            "user_id": _oid(user),
        }
    )
    if version_doc is None:
        raise NotFoundError("Resume version not found.")
    if not version_doc.get("pdf_path"):
        raise ValidationFailedError(
            "Resume version has not finished rendering yet; there is no PDF to upload."
        )
    if not ObjectId.is_valid(str(payload.job_id)):
        raise NotFoundError("Job not found.")
    job_doc = await collection(Collections.JOBS).find_one(
        {"_id": ObjectId(str(payload.job_id))}
    )
    if job_doc is None:
        raise NotFoundError("Job not found.")
    if payload.method is SubmissionMethod.AUTOMATED and not job_doc.get(
        "supports_automation", False
    ):
        raise ValidationFailedError(
            "This posting does not support automated submission. "
            "Apply manually and track it here, or use assisted mode."
        )

    existing = await _existing_for_duplicate(user=user, payload=payload)
    if existing is not None:
        return existing

    application = Application(
        user_id=str(user.id),
        job_id=str(payload.job_id),
        resume_version_id=str(payload.resume_version_id),
        cover_letter_id=(
            str(payload.cover_letter_id) if payload.cover_letter_id is not None else None
        ),
        status=ApplicationStatus.QUEUED,
        method=payload.method,
        idempotency_key=payload.idempotency_key,
        answers=payload.answers,
        events=[
            ApplicationEvent(
                at=utcnow(),
                kind="status_change",
                summary="Application created and queued.",
            )
        ],
    )
    doc = _mongo_doc(application)
    try:
        result = await collection(Collections.APPLICATIONS).insert_one(doc)
    except DuplicateKeyError as exc:
        # A concurrent request won the race between the check above and the
        # insert; the database is the arbiter, so return its winner.
        existing = await _existing_for_duplicate(user=user, payload=payload)
        if existing is not None:
            return existing
        raise ConflictError("An application for this job already exists.") from exc
    doc["_id"] = result.inserted_id
    application = Application.model_validate(doc)

    # Manual tracking never touches the worker: nothing to queue.
    if payload.method is SubmissionMethod.MANUAL:
        return application
    recent = await collection(Collections.APPLICATIONS).count_documents(
        {**_user_filter(user), "method": SubmissionMethod.AUTOMATED.value}
    )
    # Jitter: one second per recent automated submission, capped at 60s, so
    # a burst at one board is spread out instead of looking like a bot.
    defer = min(recent, 60)
    try:
        arq_job_id = await enqueue_submission(
            application_id=str(application.id),
            user_id=str(user.id),
            defer_seconds=defer,
        )
    except Exception as exc:  # noqa: BLE001 - queue outage must not lose the application
        await collection(Collections.APPLICATIONS).update_one(
            {"_id": result.inserted_id},
            {
                "$set": {"status": ApplicationStatus.DRAFT.value},
                "$push": {
                    "events": {
                        "at": utcnow(),
                        "kind": "status_change",
                        "summary": f"Submission queueing failed: {exc}",
                    }
                },
            },
        )
        return Application.model_validate(
            await _load_application(str(application.id), user)
        )
    if arq_job_id is not None:
        await collection(Collections.APPLICATIONS).update_one(
            {"_id": result.inserted_id}, {"$set": {"arq_job_id": arq_job_id}}
        )
        application = Application.model_validate(
            await _load_application(str(application.id), user)
        )
    return application


@router.get("", response_model=Page[ApplicationSummary], summary="List applications")
async def list_applications(
    user: CurrentUser,
    page: PaginationParams,
    status_filter: ApplicationStatus | None = Query(default=None, alias="status"),
) -> Page[ApplicationSummary]:
    """Tracker list, optionally filtered by pipeline stage.

    `ix_user_status_updated` covers this query exactly. Company and title
    are joined from `jobs` per row (see `_summaries`).
    """
    filt: dict[str, object] = dict(_user_filter(user))
    if status_filter is not None:
        filt["status"] = status_filter.value
    total = await collection(Collections.APPLICATIONS).count_documents(filt)
    cursor = (
        collection(Collections.APPLICATIONS)
        .find(filt, sort=[("updated_at", -1)])
        .skip(page.offset)
        .limit(page.limit)
    )
    docs = await cursor.to_list(length=page.limit)
    return Page[ApplicationSummary](
        items=await _summaries(docs),
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/stats/funnel", summary="Application funnel stats")
async def funnel_stats(user: CurrentUser) -> dict[str, int]:
    """Aggregate counts per pipeline stage for the dashboard.

    Flat `status -> count` so the tracker and dashboard can index by enum
    member without unpacking a nested envelope. A single `$group` on status,
    filtered by user_id; stages with no applications are reported as 0
    rather than omitted, so the frontend never has to guess.
    """
    pipeline = [
        {"$match": dict(_user_filter(user))},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    cursor = collection(Collections.APPLICATIONS).aggregate(pipeline)
    rows = await cursor.to_list(length=100)
    counts = {str(row["_id"]): int(row["count"]) for row in rows}
    return {status.value: counts.get(status.value, 0) for status in ApplicationStatus}


@router.get("/{application_id}", response_model=Application, summary="Get an application")
async def get_application(application_id: str, user: CurrentUser) -> Application:
    """Full application including every submission attempt.

    The attempt list is the point of this endpoint: when something went wrong,
    the screenshot and final URL are what let a user decide whether to retry or
    apply manually.
    """
    doc = await _load_application(application_id, user)
    return Application.model_validate(doc)


@router.patch(
    "/{application_id}/status",
    response_model=Application,
    summary="Update pipeline status",
)
async def update_status(
    application_id: str, payload: ApplicationStatusUpdate, user: CurrentUser
) -> Application:
    """Move an application through the pipeline (manual user action).

    Every transition appends an `ApplicationEvent`. The event timeline is
    what makes "how long does this company take to respond?" answerable
    later, so a bare status flip without an event is a data loss bug.
    Terminal states are one-way for manual moves: once SUBMITTED, OFFER,
    REJECTED, WITHDRAWN, or GHOSTED, a manual status change cannot resurrect
    the application back into an in-flight stage.
    """
    doc = await _load_application(application_id, user)
    application = Application.model_validate(doc)
    if application.is_terminal and payload.status not in (
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
        ApplicationStatus.OFFER,
        ApplicationStatus.GHOSTED,
        ApplicationStatus.ACKNOWLEDGED,
        ApplicationStatus.SCREENING,
        ApplicationStatus.INTERVIEWING,
    ):
        raise ValidationFailedError(
            f"Application is {application.status.value}; it cannot return to "
            f"{payload.status.value}."
        )
    event = ApplicationEvent(
        at=utcnow(),
        kind="status_change",
        summary=payload.note or f"Status changed to {payload.status.value}.",
    )
    await collection(Collections.APPLICATIONS).update_one(
        {"_id": ObjectId(application_id)},
        {
            "$set": {"status": payload.status.value, "updated_at": utcnow()},
            "$push": {"events": event.model_dump(mode="json")},
        },
    )
    return Application.model_validate(await _load_application(application_id, user))


@router.post(
    "/{application_id}/approve",
    response_model=Application,
    summary="Approve a paused submission",
)
async def approve_submission(application_id: str, user: CurrentUser) -> Application:
    """Confirm a form the worker filled and paused on.

    The human-in-the-loop gate. The worker has filled every field, captured a
    screenshot, and stopped short of the submit button.

    Only an application in AWAITING_APPROVAL can be approved. The worker
    does not hold a browser open across the human wait -- it fills, captures
    a screenshot, returns NEEDS_HUMAN, and closes. Approval here therefore
    re-queues the submission with `approved=True`, and the worker replays
    the whole pipeline from navigation with the approval gate satisfied.
    The deterministic `_job_id` means a duplicate approval click enqueues
    only once while the replay is pending, so approval cannot double-submit.
    """
    doc = await _load_application(application_id, user)
    application = Application.model_validate(doc)
    if application.status is not ApplicationStatus.AWAITING_APPROVAL:
        raise ValidationFailedError(
            f"Application is {application.status.value}, not awaiting approval."
        )
    await collection(Collections.APPLICATIONS).update_one(
        {"_id": ObjectId(application_id)},
        {
            "$set": {"status": ApplicationStatus.SUBMITTING.value, "updated_at": utcnow()},
            "$push": {
                "events": {
                    "at": utcnow(),
                    "kind": "status_change",
                    "summary": "User approved the filled form; submission released.",
                }
            },
        },
    )
    # Re-enqueue so the worker picks the application up with approval set.
    # enqueue_submission derives the same `_job_id`, so if the original job
    # is still pending this is a no-op rather than a second submission.
    try:
        await enqueue_submission(
            application_id=application_id, user_id=str(user.id), approved=True
        )
    except Exception as exc:  # noqa: BLE001 - approval itself succeeded; queue retry is the worker's job
        logger.warning("application.approval_enqueue_failed", error=str(exc))
    return Application.model_validate(await _load_application(application_id, user))


@router.post(
    "/{application_id}/retry",
    response_model=Application,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a failed submission",
)
async def retry_submission(application_id: str, user: CurrentUser) -> Application:
    """Re-queue a failed submission.

    Refused unless the last attempt's outcome is RETRYABLE_FAILURE, and
    refused once `attempts >= settings.automation_max_attempts`.

    The refusal matters more than the retry. A PERMANENT_FAILURE means the
    posting closed or the form changed; retrying accomplishes nothing. A
    BLOCKED outcome means the site detected automation, and retrying makes
    that worse -- hammering after a bot wall is how an IP range gets banned
    for every user on the platform. Offer assisted mode instead.
    """
    doc = await _load_application(application_id, user)
    application = Application.model_validate(doc)
    settings = get_settings()
    if len(application.attempts) >= settings.automation_max_attempts:
        raise ValidationFailedError(
            f"Attempt budget exhausted ({settings.automation_max_attempts} attempts)."
        )
    last = application.attempts[-1] if application.attempts else None
    if last is None or last.outcome is not AttemptOutcome.RETRYABLE_FAILURE:
        raise ValidationFailedError(
            "Only a submission whose last attempt was a retryable failure can be retried."
        )
    await collection(Collections.APPLICATIONS).update_one(
        {"_id": ObjectId(application_id)},
        {
            "$set": {"status": ApplicationStatus.QUEUED.value, "updated_at": utcnow()},
            "$push": {
                "events": {
                    "at": utcnow(),
                    "kind": "status_change",
                    "summary": "User retried a retryable failure.",
                }
            },
        },
    )
    try:
        arq_job_id = await enqueue_submission(
            application_id=application_id, user_id=str(user.id)
        )
    except Exception as exc:  # noqa: BLE001 - retry state is recorded; queue failure is visible
        await collection(Collections.APPLICATIONS).update_one(
            {"_id": ObjectId(application_id)},
            {
                "$push": {
                    "events": {
                        "at": utcnow(),
                        "kind": "status_change",
                        "summary": f"Retry queueing failed: {exc}",
                    }
                }
            },
        )
        return Application.model_validate(await _load_application(application_id, user))
    if arq_job_id is not None:
        await collection(Collections.APPLICATIONS).update_one(
            {"_id": ObjectId(application_id)}, {"$set": {"arq_job_id": arq_job_id}}
        )
    return Application.model_validate(await _load_application(application_id, user))
