"""Persistent application tracking and guarded automation queue operations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, cast

from bson import ObjectId
from fastapi import APIRouter, Query, Response, status
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.api.deps import CurrentUser, PaginationParams, fetch_owned
from app.core.config import get_settings
from app.core.errors import (
    ConflictError,
    NotFoundError,
    QueueUnavailableError,
    ValidationFailedError,
)
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
from app.services.queue import enqueue_submission

router = APIRouter(prefix="/applications", tags=["applications"])

_USER_TRANSITIONS: dict[ApplicationStatus, set[ApplicationStatus]] = {
    ApplicationStatus.DRAFT: {
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.WITHDRAWN,
    },
    ApplicationStatus.SUBMITTED: {
        ApplicationStatus.ACKNOWLEDGED,
        ApplicationStatus.SCREENING,
        ApplicationStatus.INTERVIEWING,
        ApplicationStatus.OFFER,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
        ApplicationStatus.GHOSTED,
    },
    ApplicationStatus.ACKNOWLEDGED: {
        ApplicationStatus.SCREENING,
        ApplicationStatus.INTERVIEWING,
        ApplicationStatus.OFFER,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
        ApplicationStatus.GHOSTED,
    },
    ApplicationStatus.SCREENING: {
        ApplicationStatus.INTERVIEWING,
        ApplicationStatus.OFFER,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
        ApplicationStatus.GHOSTED,
    },
    ApplicationStatus.INTERVIEWING: {
        ApplicationStatus.OFFER,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
        ApplicationStatus.GHOSTED,
    },
    ApplicationStatus.OFFER: {ApplicationStatus.WITHDRAWN},
    ApplicationStatus.FAILED: {ApplicationStatus.WITHDRAWN},
    ApplicationStatus.BLOCKED: {ApplicationStatus.WITHDRAWN},
    ApplicationStatus.NEEDS_REVIEW: {ApplicationStatus.WITHDRAWN},
}


def _bson_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, bytes, ObjectId, datetime)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _bson_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bson_safe(item) for item in value]
    return str(value)


def _application_to_mongo(application: Application) -> dict[str, Any]:
    doc = _bson_safe(application.model_dump(by_alias=True, exclude_none=True))
    doc.pop("_id", None)
    for field in ("user_id", "job_id", "resume_version_id", "cover_letter_id"):
        value = getattr(application, field)
        if value is not None:
            doc[field] = ObjectId(str(value))
    return cast(dict[str, Any], doc)


async def _application_for_user(application_id: str, user: CurrentUser) -> dict[str, Any]:
    return await fetch_owned(Collections.APPLICATIONS, application_id, user)


def _summary_pipeline(
    *, user_id: ObjectId, status_filter: ApplicationStatus | None, offset: int, limit: int
) -> list[dict[str, Any]]:
    match: dict[str, Any] = {"user_id": user_id}
    if status_filter is not None:
        match["status"] = status_filter.value
    return [
        {"$match": match},
        {"$sort": {"updated_at": -1}},
        {
            "$facet": {
                "items": [
                    {"$skip": offset},
                    {"$limit": limit},
                    {
                        "$lookup": {
                            "from": Collections.JOBS.value,
                            "localField": "job_id",
                            "foreignField": "_id",
                            "as": "job",
                        }
                    },
                    {"$unwind": "$job"},
                    {
                        "$project": {
                            "_id": 0,
                            "id": "$_id",
                            "job_id": 1,
                            "company_name": "$job.company_name",
                            "job_title": "$job.title",
                            "status": 1,
                            "method": 1,
                            "attempt_count": {"$size": {"$ifNull": ["$attempts", []]}},
                            "submitted_at": 1,
                            "follow_up_at": 1,
                            "updated_at": 1,
                        }
                    },
                ],
                "total": [{"$count": "value"}],
            }
        },
    ]


@router.post(
    "",
    response_model=Application,
    response_model_by_alias=False,
    status_code=status.HTTP_201_CREATED,
    summary="Track or queue an application",
)
async def submit_application(
    payload: ApplicationCreate, user: CurrentUser, response: Response
) -> Application:
    """Persist a draft, or queue only an explicitly supported automated job."""
    user_oid = ObjectId(str(user.id))
    job_oid = ObjectId(str(payload.job_id))

    existing = await collection(Collections.APPLICATIONS).find_one(
        {"user_id": user_oid, "job_id": job_oid}
    )
    if existing is None and payload.idempotency_key:
        existing = await collection(Collections.APPLICATIONS).find_one(
            {"idempotency_key": payload.idempotency_key}
        )
        if existing and existing.get("job_id") != job_oid:
            raise ConflictError("This idempotency key was already used for another job.")
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return Application.model_validate(existing)

    job = await collection(Collections.JOBS).find_one({"_id": job_oid})
    if job is None:
        raise NotFoundError("Job posting not found.")
    if payload.method is SubmissionMethod.AUTOMATED:
        if job.get("status") != "open":
            raise ConflictError("Only a job marked open can be queued for automation.")
        if not job.get("supports_automation"):
            raise ConflictError("This posting has no authorized automation adapter.")
        if not job.get("source", {}).get("url"):
            raise ValidationFailedError("The job posting has no application URL.")

    version_doc = await collection(Collections.RESUME_VERSIONS).find_one(
        {
            "_id": ObjectId(str(payload.resume_version_id)),
            "user_id": user_oid,
            "pdf_path": {"$nin": [None, ""]},
        }
    )
    if version_doc is None:
        raise ConflictError("Choose a rendered resume version that belongs to you.")

    now = utcnow()
    automated = payload.method is SubmissionMethod.AUTOMATED
    application = Application(
        user_id=str(user_oid),
        job_id=str(job_oid),
        resume_version_id=str(version_doc["_id"]),
        cover_letter_id=payload.cover_letter_id,
        status=ApplicationStatus.QUEUED if automated else ApplicationStatus.DRAFT,
        method=payload.method,
        idempotency_key=payload.idempotency_key,
        answers=payload.answers,
        events=[
            ApplicationEvent(
                at=now,
                kind="created",
                summary=(
                    "Queued for the automation worker; a person will approve before submission."
                    if automated
                    else (
                        "Added to the application tracker as a draft. "
                        "No external submission was made."
                    )
                ),
            )
        ],
    )
    try:
        inserted = await collection(Collections.APPLICATIONS).insert_one(
            _application_to_mongo(application)
        )
    except DuplicateKeyError:
        existing = await collection(Collections.APPLICATIONS).find_one(
            {"user_id": user_oid, "job_id": job_oid}
        )
        if existing is None and payload.idempotency_key:
            existing = await collection(Collections.APPLICATIONS).find_one(
                {"idempotency_key": payload.idempotency_key}
            )
        if existing is None:
            raise ConflictError("This application conflicts with an existing record.") from None
        response.status_code = status.HTTP_200_OK
        return Application.model_validate(existing)

    created = application.model_copy(update={"id": str(inserted.inserted_id)})
    if not automated:
        return created

    response.status_code = status.HTTP_202_ACCEPTED
    try:
        arq_job_id = await enqueue_submission(
            application_id=str(inserted.inserted_id),
            user_id=str(user_oid),
            attempt_number=1,
        )
    except Exception as exc:
        await collection(Collections.APPLICATIONS).update_one(
            {"_id": inserted.inserted_id, "user_id": user_oid},
            {
                "$set": {"status": ApplicationStatus.FAILED.value, "updated_at": utcnow()},
                "$push": {
                    "events": {
                        "at": utcnow(),
                        "kind": "queue_failure",
                        "summary": "The automation queue was unavailable; no submission was sent.",
                        "metadata": {},
                    }
                },
            },
        )
        raise QueueUnavailableError() from exc
    await collection(Collections.APPLICATIONS).update_one(
        {"_id": inserted.inserted_id, "user_id": user_oid},
        {"$set": {"arq_job_id": arq_job_id, "updated_at": utcnow()}},
    )
    return created.model_copy(update={"arq_job_id": arq_job_id})


@router.get("", response_model=Page[ApplicationSummary], summary="List applications")
async def list_applications(
    user: CurrentUser,
    page: PaginationParams,
    status_filter: ApplicationStatus | None = Query(default=None, alias="status"),
) -> Page[ApplicationSummary]:
    rows = (
        await collection(Collections.APPLICATIONS)
        .aggregate(
            _summary_pipeline(
                user_id=ObjectId(str(user.id)),
                status_filter=status_filter,
                offset=page.offset,
                limit=page.limit,
            )
        )
        .to_list(length=1)
    )
    facet = rows[0] if rows else {"items": [], "total": []}
    totals = facet.get("total", [])
    total = int(totals[0]["value"]) if totals else 0
    return Page[ApplicationSummary](
        items=[ApplicationSummary.model_validate(item) for item in facet.get("items", [])],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/stats/funnel", summary="Application funnel stats")
async def funnel_stats(user: CurrentUser) -> dict[str, int]:
    counts = {item.value: 0 for item in ApplicationStatus}
    rows = (
        await collection(Collections.APPLICATIONS)
        .aggregate(
            [
                {"$match": {"user_id": ObjectId(str(user.id))}},
                {"$group": {"_id": "$status", "count": {"$sum": 1}}},
            ]
        )
        .to_list(length=len(counts))
    )
    counts.update({str(row["_id"]): int(row["count"]) for row in rows})
    return counts


@router.get(
    "/{application_id}",
    response_model=Application,
    response_model_by_alias=False,
    summary="Get an application",
)
async def get_application(application_id: str, user: CurrentUser) -> Application:
    return Application.model_validate(await _application_for_user(application_id, user))


@router.patch(
    "/{application_id}/status",
    response_model=Application,
    response_model_by_alias=False,
    summary="Update an application status",
)
async def update_status(
    application_id: str, payload: ApplicationStatusUpdate, user: CurrentUser
) -> Application:
    doc = await _application_for_user(application_id, user)
    application = Application.model_validate(doc)
    allowed = _USER_TRANSITIONS.get(application.status, set())
    if payload.status not in allowed:
        raise ConflictError(
            f"Cannot move an application from {application.status.value} to {payload.status.value}."
        )
    now = utcnow()
    summary = payload.note or f"Status changed to {payload.status.value.replace('_', ' ')}."
    updated = await collection(Collections.APPLICATIONS).find_one_and_update(
        {
            "_id": ObjectId(application_id),
            "user_id": ObjectId(str(user.id)),
            "status": application.status.value,
        },
        {
            "$set": {
                "status": payload.status.value,
                "updated_at": now,
                **({"submitted_at": now} if payload.status is ApplicationStatus.SUBMITTED else {}),
            },
            "$push": {
                "events": ApplicationEvent(
                    at=now, kind="status_change", summary=summary
                ).model_dump()
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise ConflictError("The application changed; reload and try again.")
    return Application.model_validate(updated)


@router.post(
    "/{application_id}/approve",
    response_model=Application,
    response_model_by_alias=False,
    summary="Approve a paused submission",
)
async def approve_submission(application_id: str, user: CurrentUser) -> Application:
    doc = await _application_for_user(application_id, user)
    application = Application.model_validate(doc)
    if application.status is not ApplicationStatus.AWAITING_APPROVAL:
        raise ConflictError("This application is not waiting for approval.")
    attempt_number = max(1, len(application.attempts) + 1)
    try:
        arq_job_id = await enqueue_submission(
            application_id=str(application.id),
            user_id=str(user.id),
            approved=True,
            attempt_number=attempt_number,
        )
    except Exception as exc:
        raise QueueUnavailableError() from exc
    now = utcnow()
    updated = await collection(Collections.APPLICATIONS).find_one_and_update(
        {
            "_id": ObjectId(application_id),
            "user_id": ObjectId(str(user.id)),
            "status": ApplicationStatus.AWAITING_APPROVAL.value,
        },
        {
            "$set": {
                "status": ApplicationStatus.QUEUED.value,
                "arq_job_id": arq_job_id,
                "updated_at": now,
            },
            "$push": {
                "events": {
                    "at": now,
                    "kind": "approval",
                    "summary": "Approved; queued for a fresh form fill and verification.",
                    "metadata": {},
                }
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise ConflictError("The application changed; reload before approving it.")
    return Application.model_validate(updated)


@router.post(
    "/{application_id}/retry",
    response_model=Application,
    response_model_by_alias=False,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a safely retryable submission",
)
async def retry_submission(application_id: str, user: CurrentUser) -> Application:
    doc = await _application_for_user(application_id, user)
    application = Application.model_validate(doc)
    settings = get_settings()
    if application.status is not ApplicationStatus.FAILED:
        raise ConflictError("Only failed applications can be retried.")
    if (
        not application.attempts
        or application.attempts[-1].outcome is not AttemptOutcome.RETRYABLE_FAILURE
    ):
        raise ConflictError("The last attempt was not confirmed safe to retry.")
    if len(application.attempts) >= settings.automation_max_attempts:
        raise ConflictError("The maximum number of attempts has been reached.")
    attempt_number = len(application.attempts) + 1
    try:
        arq_job_id = await enqueue_submission(
            application_id=str(application.id),
            user_id=str(user.id),
            attempt_number=attempt_number,
        )
    except Exception as exc:
        raise QueueUnavailableError() from exc
    now = utcnow()
    updated = await collection(Collections.APPLICATIONS).find_one_and_update(
        {
            "_id": ObjectId(application_id),
            "user_id": ObjectId(str(user.id)),
            "status": ApplicationStatus.FAILED.value,
        },
        {
            "$set": {
                "status": ApplicationStatus.QUEUED.value,
                "arq_job_id": arq_job_id,
                "updated_at": now,
            },
            "$push": {
                "events": {
                    "at": now,
                    "kind": "retry",
                    "summary": f"Queued safe retry attempt {attempt_number}.",
                    "metadata": {},
                }
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise ConflictError("The application changed; reload before retrying.")
    return Application.model_validate(updated)
