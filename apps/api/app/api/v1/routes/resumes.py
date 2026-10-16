"""Resume routes: CRUD, the conversational generation agent, and rendering.

Two things worth reading before extending this module:

1. Generation is *conversational and resumable*. The agent interrupts to ask
   for a missing metric, and the graph state is checkpointed to Mongo, so a
   user can close the tab and come back tomorrow. The endpoints below are
   therefore turn-based (`start`, then repeated `turn`) rather than one
   request/response.

2. Rendering is asynchronous and produces an immutable version. `POST
   /render` creates the `ResumeVersion` synchronously (cheap -- it snapshots
   data we already hold) and enqueues the LaTeX compile (expensive). The
   client polls the render job.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from bson import ObjectId
from fastapi import APIRouter, Query, status
from fastapi.responses import FileResponse
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from pymongo import ReturnDocument

from app.agents.resume.state import initial_state
from app.api.deps import CurrentUser, PaginationParams, fetch_owned
from app.core.config import get_settings
from app.core.errors import (
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    QueueUnavailableError,
    RateLimitedError,
    ValidationFailedError,
)
from app.core.logging import get_logger
from app.db.mongo import Collections, collection, get_client
from app.models.agent import (
    AgentThread,
    BulletProposal,
    GenerateStartRequest,
    GenerateTurnRequest,
    GenerateTurnResponse,
    ProposalDecision,
    ThreadKind,
    ThreadStatus,
)
from app.models.base import Page, utcnow
from app.models.bullet import (
    Bullet,
    BulletStatus,
    ProposalReviewDecision,
)
from app.models.profile import CareerProfile
from app.models.resume import (
    RenderedBullet,
    RenderedEntry,
    RenderedSection,
    RenderJob,
    RenderJobStatus,
    RenderRequest,
    Resume,
    ResumeCreate,
    ResumeSection,
    ResumeStatus,
    ResumeSummary,
    ResumeTemplate,
    ResumeUpdate,
    ResumeVersion,
    SectionKind,
)
from app.services.queue import enqueue_render, render_queue

router = APIRouter(prefix="/resumes", tags=["resumes"])
logger = get_logger(__name__)


def _bson_safe(value: Any) -> Any:
    """Convert Pydantic values to BSON-compatible primitives."""
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


def _resume_to_mongo(resume: Resume) -> dict[str, Any]:
    """Persist Mongo references as ObjectIds, not their API string forms."""
    doc = _bson_safe(resume.model_dump(by_alias=True, exclude_none=True))
    doc.pop("_id", None)
    doc["user_id"] = ObjectId(str(resume.user_id))
    if resume.target_job_id is not None:
        doc["target_job_id"] = ObjectId(str(resume.target_job_id))
    for section in doc.get("sections", []):
        section["bullet_ids"] = [ObjectId(str(value)) for value in section["bullet_ids"]]
    return dict(doc)


def _sections_to_mongo(sections: list[ResumeSection]) -> list[dict[str, Any]]:
    rows = _bson_safe([section.model_dump() for section in sections])
    for section in rows:
        section["bullet_ids"] = [ObjectId(str(value)) for value in section["bullet_ids"]]
    return list(rows)


def _date_label(start: date | None, end: date | None = None) -> str:
    if start is None:
        return "Present" if end is None else end.strftime("%b %Y")
    return f"{start.strftime('%b %Y')} – {end.strftime('%b %Y') if end else 'Present'}"


def _build_resume_snapshot(
    resume: Resume,
    profile: CareerProfile,
    bullets: list[Bullet],
    *,
    version_number: int,
    template: ResumeTemplate,
    change_note: str | None,
) -> ResumeVersion:
    """Freeze the selected profile data and approved bullet text for rendering."""
    bullet_by_id = {str(bullet.id): bullet for bullet in bullets}
    experience_by_id = {entry.entry_id: entry for entry in profile.experiences}
    project_by_id = {entry.entry_id: entry for entry in profile.projects}
    education_by_id = {entry.entry_id: entry for entry in profile.education}
    certification_by_id = {entry.entry_id: entry for entry in profile.certifications}
    visible = {section.kind for section in resume.sections if section.visible}

    rendered_sections: list[RenderedSection] = []
    for section in sorted(resume.sections, key=lambda item: item.order):
        if not section.visible or section.kind not in {
            SectionKind.EXPERIENCE,
            SectionKind.PROJECTS,
        }:
            continue
        rendered_entries = []
        for entry_id in section.entry_ids:
            selected_bullets = [
                bullet_by_id[bullet_id]
                for bullet_id in section.bullet_ids
                if str(bullet_by_id[bullet_id].profile_entry_id) == entry_id
            ]
            subheading: str | None
            if section.kind is SectionKind.EXPERIENCE:
                experience = experience_by_id[entry_id]
                heading = f"{experience.title}, {experience.company}"
                subheading = experience.employment_type.value.replace("_", " ").title()
                date_label = _date_label(experience.dates.start, experience.dates.end)
                location = experience.location
            else:
                project = project_by_id[entry_id]
                heading = project.name
                subheading = project.role
                date_label = (
                    _date_label(project.dates.start, project.dates.end) if project.dates else ""
                )
                location = None
            rendered_entries.append(
                RenderedEntry(
                    entry_id=entry_id,
                    heading=heading,
                    subheading=subheading,
                    date_label=date_label,
                    location=location,
                    bullets=[
                        RenderedBullet(
                            bullet_id=str(bullet.id),
                            text=bullet.text,
                            skills=bullet.skills,
                        )
                        for bullet in selected_bullets
                    ],
                )
            )
        if rendered_entries:
            rendered_sections.append(
                RenderedSection(
                    kind=section.kind,
                    title=section.title,
                    order=section.order,
                    entries=rendered_entries,
                )
            )

    return ResumeVersion(
        resume_id=str(resume.id),
        user_id=str(resume.user_id),
        version=version_number,
        template=template,
        contact=resume.contact,
        summary=resume.summary if SectionKind.SUMMARY in visible else None,
        sections=rendered_sections,
        skills=profile.skills if SectionKind.SKILLS in visible else [],
        education=[
            education_by_id[entry_id]
            for section in resume.sections
            if section.visible and section.kind is SectionKind.EDUCATION
            for entry_id in section.entry_ids
        ],
        certifications=[
            certification_by_id[entry_id]
            for section in resume.sections
            if section.visible and section.kind is SectionKind.CERTIFICATIONS
            for entry_id in section.entry_ids
        ],
        change_note=change_note,
    )


def _version_to_mongo(version: ResumeVersion) -> dict[str, Any]:
    doc = _bson_safe(version.model_dump(by_alias=True, exclude_none=True))
    doc.pop("_id", None)
    doc["resume_id"] = ObjectId(str(version.resume_id))
    doc["user_id"] = ObjectId(str(version.user_id))
    for section in doc.get("sections", []):
        for entry in section.get("entries", []):
            for bullet in entry.get("bullets", []):
                bullet["bullet_id"] = ObjectId(str(bullet["bullet_id"]))
    return dict(doc)


def _render_job_to_mongo(job: RenderJob) -> dict[str, Any]:
    doc = _bson_safe(job.model_dump(by_alias=True, exclude_none=True))
    doc.pop("_id", None)
    for field in ("user_id", "resume_id", "version_id"):
        doc[field] = ObjectId(str(getattr(job, field)))
    return dict(doc)


async def _enforce_agent_rate_limit(user_id: str) -> None:
    """Bound per-user graph/model turns with an hourly Redis counter."""
    now = utcnow()
    key = f"resunet:agent-limit:{user_id}:{now:%Y%m%d%H}"
    async with render_queue().pipeline(transaction=True) as pipeline:
        pipeline.incr(key)
        pipeline.expire(key, 3600)
        count, _ = await pipeline.execute()
    limit = get_settings().agent_rate_limit_per_hour
    if count > limit:
        raise RateLimitedError(f"Resume generation is limited to {limit} turns per user per hour.")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=Page[ResumeSummary], summary="List resumes")
async def list_resumes(user: CurrentUser, page: PaginationParams) -> Page[ResumeSummary]:
    """Return the caller's stored resumes, newest first."""
    user_oid = ObjectId(str(user.id))
    resumes = collection(Collections.RESUMES)
    query = {"user_id": user_oid}
    docs = await (
        resumes.find(query)
        .sort("updated_at", -1)
        .skip(page.offset)
        .limit(page.limit)
        .to_list(length=page.limit)
    )
    items = []
    for doc in docs:
        summary_doc: dict[str, Any] = {
            "id": doc.get("_id"),
            **{
                key: doc[key]
                for key in (
                    "title",
                    "status",
                    "template",
                    "current_version",
                    "updated_at",
                )
                if key in doc
            },
            "target_role": doc.get("target_role"),
            "latest_ats_score": doc.get("latest_ats_score"),
        }
        items.append(ResumeSummary.model_validate(summary_doc))
    return Page[ResumeSummary](
        items=items,
        total=await resumes.count_documents(query),
        limit=page.limit,
        offset=page.offset,
    )


@router.post(
    "",
    response_model=Resume,
    response_model_by_alias=False,
    status_code=status.HTTP_201_CREATED,
    summary="Create a resume",
)
async def create_resume(payload: ResumeCreate, user: CurrentUser) -> Resume:
    """Create a stored working copy initialized from the caller's profile."""
    user_oid = ObjectId(str(user.id))
    profile_doc = await collection(Collections.CAREER_PROFILES).find_one({"user_id": user_oid})
    if profile_doc is None:
        raise NotFoundError("Career profile not found.")
    profile = CareerProfile.model_validate(profile_doc)

    if payload.target_job_id is not None:
        job = await collection(Collections.JOBS).find_one(
            {"_id": ObjectId(str(payload.target_job_id)), "status": "open"}
        )
        if job is None:
            raise NotFoundError("Open target job not found.")

    sections = [
        ResumeSection(kind=SectionKind.SUMMARY, title="Summary", order=0),
        ResumeSection(
            kind=SectionKind.EXPERIENCE,
            title="Experience",
            order=1,
            entry_ids=[entry.entry_id for entry in profile.experiences],
        ),
        ResumeSection(
            kind=SectionKind.PROJECTS,
            title="Projects",
            order=2,
            entry_ids=[entry.entry_id for entry in profile.projects],
        ),
        ResumeSection(
            kind=SectionKind.EDUCATION,
            title="Education",
            order=3,
            entry_ids=[entry.entry_id for entry in profile.education],
        ),
        ResumeSection(kind=SectionKind.SKILLS, title="Skills", order=4),
        ResumeSection(
            kind=SectionKind.CERTIFICATIONS,
            title="Certifications",
            order=5,
            entry_ids=[entry.entry_id for entry in profile.certifications],
        ),
    ]
    resume = Resume(
        user_id=str(user_oid),
        title=payload.title,
        template=payload.template,
        target_job_id=payload.target_job_id,
        target_role=payload.target_role,
        contact=profile.contact,
        summary=profile.summary,
        sections=sections,
    )
    inserted = await collection(Collections.RESUMES).insert_one(_resume_to_mongo(resume))
    return resume.model_copy(update={"id": str(inserted.inserted_id)})


@router.get(
    "/{resume_id}",
    response_model=Resume,
    response_model_by_alias=False,
    summary="Get a resume",
)
async def get_resume(resume_id: str, user: CurrentUser) -> Resume:
    """Fetch the caller's resume. Archived resumes remain readable."""
    doc = await fetch_owned(Collections.RESUMES, resume_id, user)
    return Resume.model_validate(doc)


@router.patch(
    "/{resume_id}",
    response_model=Resume,
    response_model_by_alias=False,
    summary="Update a resume",
)
async def update_resume(resume_id: str, payload: ResumeUpdate, user: CurrentUser) -> Resume:
    """Apply only provided fields; explicit nulls clear nullable values."""
    doc = await fetch_owned(Collections.RESUMES, resume_id, user)
    if doc.get("status") == ResumeStatus.ARCHIVED.value:
        raise NotFoundError()

    updates = payload.model_dump(exclude_unset=True)
    if updates.get("status") == ResumeStatus.ARCHIVED:
        raise ValidationFailedError("Archive a resume with DELETE, not PATCH.")
    if updates.get("status") == ResumeStatus.GENERATING:
        raise ValidationFailedError("Generation state is managed by the agent workflow.")

    if "sections" in updates and updates["sections"] is not None:
        section_models = [ResumeSection.model_validate(row) for row in updates["sections"]]
        await _validate_resume_sections(section_models, user)
        updates["sections"] = _sections_to_mongo(section_models)
    updates["updated_at"] = utcnow()
    updated = await collection(Collections.RESUMES).find_one_and_update(
        {"_id": ObjectId(resume_id), "user_id": ObjectId(str(user.id))},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise NotFoundError()
    return Resume.model_validate(updated)


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Archive a resume")
async def delete_resume(resume_id: str, user: CurrentUser) -> None:
    """Archive a resume without removing versions referenced by applications."""
    existing = await fetch_owned(Collections.RESUMES, resume_id, user)
    if existing.get("status") == ResumeStatus.ARCHIVED.value:
        return
    result = await collection(Collections.RESUMES).update_one(
        {"_id": ObjectId(resume_id), "user_id": ObjectId(str(user.id))},
        {"$set": {"status": ResumeStatus.ARCHIVED.value, "updated_at": utcnow()}},
    )
    if result.matched_count == 0:
        raise NotFoundError()
    return


async def _load_profile_for_user(user: CurrentUser, *, session: Any | None = None) -> CareerProfile:
    query = {"user_id": ObjectId(str(user.id))}
    profiles = collection(Collections.CAREER_PROFILES)
    doc = (
        await profiles.find_one(query, session=session)
        if session is not None
        else await profiles.find_one(query)
    )
    if doc is None:
        raise NotFoundError("Career profile not found.")
    return CareerProfile.model_validate(doc)


async def _load_approved_bullets(
    sections: list[ResumeSection],
    user: CurrentUser,
    profile: CareerProfile,
    *,
    session: Any | None = None,
) -> list[Bullet]:
    """Validate section relationships, then return owned approved bullets."""
    kinds = [section.kind for section in sections]
    orders = [section.order for section in sections]
    if len(kinds) != len(set(kinds)) or len(orders) != len(set(orders)):
        raise ValidationFailedError("Resume sections must have unique kinds and positions.")

    entries_by_kind = {
        SectionKind.EXPERIENCE: {entry.entry_id for entry in profile.experiences},
        SectionKind.PROJECTS: {entry.entry_id for entry in profile.projects},
        SectionKind.EDUCATION: {entry.entry_id for entry in profile.education},
        SectionKind.CERTIFICATIONS: {entry.entry_id for entry in profile.certifications},
    }
    seen_entries: set[str] = set()
    seen_bullets: set[str] = set()
    for section in sections:
        if section.kind in {
            SectionKind.EXPERIENCE,
            SectionKind.PROJECTS,
            SectionKind.EDUCATION,
            SectionKind.CERTIFICATIONS,
        }:
            allowed = entries_by_kind[section.kind]
            if not set(section.entry_ids).issubset(allowed):
                raise ValidationFailedError(
                    "A resume section references an entry of the wrong kind."
                )
        elif section.entry_ids:
            raise ValidationFailedError(
                "Summary and skills sections cannot reference profile entries."
            )
        if (
            section.kind not in {SectionKind.EXPERIENCE, SectionKind.PROJECTS}
            and section.bullet_ids
        ):
            raise ValidationFailedError(
                "Bullets can only be selected for experience or project sections."
            )
        if seen_entries.intersection(section.entry_ids):
            raise ValidationFailedError("A profile entry cannot appear in multiple sections.")
        if seen_bullets.intersection(map(str, section.bullet_ids)):
            raise ValidationFailedError("A bullet cannot appear more than once in a resume.")
        seen_entries.update(section.entry_ids)
        seen_bullets.update(map(str, section.bullet_ids))

    if not seen_bullets:
        return []

    user_oid = ObjectId(str(user.id))
    query = {
        "_id": {"$in": [ObjectId(bullet_id) for bullet_id in seen_bullets]},
        "user_id": user_oid,
        "status": BulletStatus.APPROVED.value,
    }
    bullets_collection = collection(Collections.BULLETS)
    cursor = (
        bullets_collection.find(query, session=session)
        if session is not None
        else bullets_collection.find(query)
    )
    docs = await cursor.to_list(length=len(seen_bullets))
    found = {str(doc["_id"]): doc for doc in docs}
    if found.keys() != seen_bullets:
        raise ValidationFailedError("Resume sections may only include your approved bullets.")
    bullet_models = [Bullet.model_validate(doc) for doc in docs]
    by_id = {str(bullet.id): bullet for bullet in bullet_models}
    for section in sections:
        selected = [by_id[bullet_id] for bullet_id in map(str, section.bullet_ids)]
        if any(str(bullet.profile_entry_id) not in section.entry_ids for bullet in selected):
            raise ValidationFailedError(
                "A selected bullet must belong to an included profile entry."
            )
        if any(
            str(bullet.profile_entry_id) not in profile.bullet_parent_ids() for bullet in selected
        ):
            raise ValidationFailedError("A bullet no longer belongs to an active profile entry.")
    return bullet_models


async def _validate_resume_sections(sections: list[ResumeSection], user: CurrentUser) -> None:
    profile = await _load_profile_for_user(user)
    await _load_approved_bullets(sections, user, profile)


# ---------------------------------------------------------------------------
# Conversational generation (LangGraph)
# ---------------------------------------------------------------------------


@router.post(
    "/generate/start",
    response_model=GenerateTurnResponse,
    summary="Open a resume-generation conversation",
)
async def start_generation(
    payload: GenerateStartRequest, user: CurrentUser
) -> GenerateTurnResponse:
    """Start a Mongo-checkpointed generation run for a real profile entry."""
    profile = await _load_profile_for_user(user)
    if payload.resume_id is None:
        raise ValidationFailedError("Create or choose a resume before starting generation.")
    resume_doc = await fetch_owned(Collections.RESUMES, str(payload.resume_id), user)
    resume = Resume.model_validate(resume_doc)

    available_ids = {
        entry_id
        for section in resume.sections
        if section.visible and section.kind in {SectionKind.EXPERIENCE, SectionKind.PROJECTS}
        for entry_id in section.entry_ids
    }
    entry_id = payload.profile_entry_id
    if entry_id is None:
        if len(available_ids) != 1:
            raise ValidationFailedError(
                "Choose one experience or project entry before generating bullets."
            )
        entry_id = next(iter(available_ids))
    entry = profile.find_entry(entry_id)
    if entry is None or entry_id not in available_ids:
        raise ValidationFailedError(
            "The selected entry must be an active experience or project in this resume."
        )

    raw_notes = (payload.seed_notes or entry.raw_notes).strip()
    if not raw_notes:
        raise ValidationFailedError("Add notes about this entry before starting generation.")
    await _enforce_agent_rate_limit(str(user.id))
    target_keywords: list[str] = []
    if resume.target_job_id is not None:
        job_doc = await collection(Collections.JOBS).find_one(
            {"_id": ObjectId(str(resume.target_job_id)), "status": "open"},
            {"parsed.keywords.term": 1},
        )
        if job_doc:
            target_keywords = [
                str(keyword["term"])
                for keyword in job_doc.get("parsed", {}).get("keywords", [])
                if isinstance(keyword, dict) and keyword.get("term")
            ]

    thread_id = f"gen_{uuid4().hex}"
    now = utcnow()
    thread = AgentThread(
        user_id=str(user.id),
        kind=ThreadKind.RESUME_GENERATION,
        thread_id=thread_id,
        resume_id=str(resume.id),
        profile_entry_id=entry_id,
        status=ThreadStatus.ACTIVE,
        title=(getattr(entry, "title", None) or getattr(entry, "name", None) or "Resume bullets"),
        message_count=1,
        last_message_at=now,
    )
    thread_doc = thread.to_mongo()
    thread_doc["user_id"] = ObjectId(str(user.id))
    thread_doc["resume_id"] = ObjectId(str(resume.id))
    await collection(Collections.AGENT_THREADS).insert_one(thread_doc)
    await collection(Collections.RESUMES).update_one(
        {"_id": ObjectId(str(resume.id)), "user_id": ObjectId(str(user.id))},
        {"$set": {"thread_id": thread_id, "status": ResumeStatus.GENERATING.value}},
    )

    context = {
        "title": getattr(entry, "title", None) or getattr(entry, "role", None),
        "company": getattr(entry, "company", None) or getattr(entry, "name", None),
        "tech_stack": ", ".join(entry.tech_stack),
    }
    initial = initial_state(
        user_id=str(user.id),
        thread_id=thread_id,
        resume_id=str(resume.id),
        profile_entry_id=entry_id,
        target_job_id=str(resume.target_job_id) if resume.target_job_id else None,
        raw_notes=raw_notes,
        target_keywords=target_keywords,
    )
    initial["entry_context"] = context
    previous_status = resume.status.value
    try:
        from app.agents.resume import get_compiled_graph

        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        graph = await get_compiled_graph()
        result = await graph.ainvoke(initial, config=config)
        snapshot = await graph.aget_state(config)
        response = _generation_result(thread_id, result, snapshot.values)
        await collection(Collections.AGENT_THREADS).update_one(
            {"thread_id": thread_id, "user_id": ObjectId(str(user.id))},
            {
                "$set": {
                    "status": response.status.value,
                    "updated_at": utcnow(),
                }
            },
        )
        await collection(Collections.RESUMES).update_one(
            {"_id": ObjectId(str(resume.id)), "user_id": ObjectId(str(user.id))},
            {
                "$set": {
                    "status": ResumeStatus.GENERATING.value
                    if response.status in {ThreadStatus.ACTIVE, ThreadStatus.AWAITING_USER}
                    else ResumeStatus.DRAFT.value
                }
            },
        )
        return response
    except Exception as exc:
        await collection(Collections.AGENT_THREADS).update_one(
            {"thread_id": thread_id, "user_id": ObjectId(str(user.id))},
            {"$set": {"status": ThreadStatus.FAILED.value, "updated_at": utcnow()}},
        )
        await collection(Collections.RESUMES).update_one(
            {"_id": ObjectId(str(resume.id)), "user_id": ObjectId(str(user.id))},
            {"$set": {"status": previous_status}},
        )
        logger.error(
            "generation.start_failed",
            thread_id=thread_id,
            error_type=type(exc).__name__,
        )
        if isinstance(exc, ExternalServiceError):
            raise
        raise ExternalServiceError(
            "The resume generation service could not complete the request."
        ) from exc


@router.post(
    "/generate/{thread_id}/turn",
    response_model=GenerateTurnResponse,
    summary="Continue a generation conversation",
)
async def generation_turn(
    thread_id: str, payload: GenerateTurnRequest, user: CurrentUser
) -> GenerateTurnResponse:
    """Resume only the caller's checkpointed generation thread."""
    thread = await collection(Collections.AGENT_THREADS).find_one(
        {"thread_id": thread_id, "user_id": ObjectId(str(user.id))}
    )
    if thread is None:
        raise NotFoundError()
    if thread.get("status") != ThreadStatus.AWAITING_USER.value:
        raise ConflictError("This generation thread is not waiting for a user response.")
    await _enforce_agent_rate_limit(str(user.id))

    try:
        from app.agents.resume import get_compiled_graph

        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        graph = await get_compiled_graph()
        result = await graph.ainvoke(Command(resume=payload.message), config=config)
        snapshot = await graph.aget_state(config)
        response = _generation_result(thread_id, result, snapshot.values)
    except Exception as exc:
        logger.error(
            "generation.turn_failed",
            thread_id=thread_id,
            error_type=type(exc).__name__,
        )
        if isinstance(exc, ExternalServiceError):
            raise
        raise ExternalServiceError(
            "The resume generation service could not complete the turn."
        ) from exc

    await collection(Collections.AGENT_THREADS).update_one(
        {"thread_id": thread_id, "user_id": ObjectId(str(user.id))},
        {
            "$set": {
                "status": response.status.value,
                "last_message_at": utcnow(),
                "updated_at": utcnow(),
            },
            "$inc": {"message_count": 1},
        },
    )
    resume_id = thread.get("resume_id")
    if isinstance(resume_id, ObjectId):
        await collection(Collections.RESUMES).update_one(
            {"_id": resume_id, "user_id": ObjectId(str(user.id))},
            {
                "$set": {
                    "status": ResumeStatus.GENERATING.value
                    if response.status in {ThreadStatus.ACTIVE, ThreadStatus.AWAITING_USER}
                    else ResumeStatus.DRAFT.value
                }
            },
        )
    return response


def _generation_result(
    thread_id: str, result: dict[str, Any], state: dict[str, Any]
) -> GenerateTurnResponse:
    """Translate checkpoint output and graph interrupts to the public contract."""
    interrupts = result.get("__interrupt__", ())
    interrupt_value = next(
        (getattr(item, "value", None) for item in interrupts if getattr(item, "value", None)),
        None,
    )
    question = (
        interrupt_value.get("question")
        if isinstance(interrupt_value, dict)
        else state.get("pending_question")
    )
    graph_status = state.get("status")
    thread_status = (
        ThreadStatus.AWAITING_USER
        if question or graph_status == "awaiting_user"
        else ThreadStatus.COMPLETED
        if graph_status == "done"
        else ThreadStatus.ACTIVE
    )
    messages = state.get("messages", [])
    assistant_message = next(
        (
            str(message.get("content", ""))
            for message in reversed(messages)
            if message.get("role") == "assistant" and message.get("content")
        ),
        str(question or "Tell me more about the result you achieved."),
    )
    if question:
        assistant_message = str(question)
    proposals = [
        BulletProposal.model_validate(item)
        for item in [*state.get("completed_bullets", []), *state.get("flagged_bullets", [])]
    ]
    persisted_ids = state.get("persisted_bullet_ids", [])
    if persisted_ids:
        proposals = [
            proposal.model_copy(update={"bullet_id": persisted_ids[index]})
            if index < len(persisted_ids)
            else proposal
            for index, proposal in enumerate(proposals)
        ]
    return GenerateTurnResponse(
        thread_id=thread_id,
        status=thread_status,
        assistant_message=assistant_message,
        question=str(question) if question else None,
        proposals=proposals,
        needs_input=thread_status is ThreadStatus.AWAITING_USER,
    )


@router.get(
    "/{resume_id}/generation",
    response_model=GenerateTurnResponse,
    summary="Restore the current generation session",
)
async def get_generation_session(resume_id: str, user: CurrentUser) -> GenerateTurnResponse:
    """Restore this user's latest graph state after a page reload."""
    resume_doc = await fetch_owned(Collections.RESUMES, resume_id, user)
    thread_id = resume_doc.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise NotFoundError("This resume has no generation session yet.")
    thread = await collection(Collections.AGENT_THREADS).find_one(
        {
            "thread_id": thread_id,
            "resume_id": ObjectId(resume_id),
            "user_id": ObjectId(str(user.id)),
            "kind": ThreadKind.RESUME_GENERATION.value,
        }
    )
    if thread is None:
        raise NotFoundError()
    try:
        from app.agents.resume import get_compiled_graph

        graph = await get_compiled_graph()
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        snapshot = await graph.aget_state(config)
        response = _generation_result(thread_id, {}, snapshot.values)
        saved_bullets = await (
            collection(Collections.BULLETS)
            .find({"user_id": ObjectId(str(user.id)), "thread_id": thread_id})
            .to_list(length=100)
        )
        valid_decisions = {item.value for item in ProposalReviewDecision}
        decisions = {
            str(item["_id"]): item.get("review_decision")
            for item in saved_bullets
            if item.get("review_decision") in valid_decisions
        }
        response.proposals = [
            proposal.model_copy(update={"decision": decisions.get(str(proposal.bullet_id))})
            for proposal in response.proposals
        ]
        return response
    except Exception as exc:
        logger.error(
            "generation.restore_failed",
            thread_id=thread_id,
            error_type=type(exc).__name__,
        )
        raise ExternalServiceError(
            "The saved resume generation session could not be restored."
        ) from exc


@router.post(
    "/generate/{thread_id}/proposals/{bullet_id}/decision",
    response_model=Bullet,
    response_model_by_alias=False,
    summary="Accept, reject, or revise a generated bullet",
)
async def decide_generation_proposal(
    thread_id: str,
    bullet_id: str,
    payload: ProposalDecision,
    user: CurrentUser,
) -> Bullet:
    """Persist a user's decision and attach accepted content to its resume."""
    if not ObjectId.is_valid(bullet_id):
        raise NotFoundError()
    user_oid = ObjectId(str(user.id))
    thread = await collection(Collections.AGENT_THREADS).find_one(
        {
            "thread_id": thread_id,
            "user_id": user_oid,
            "kind": ThreadKind.RESUME_GENERATION.value,
        }
    )
    if thread is None or thread.get("status") != ThreadStatus.COMPLETED.value:
        raise NotFoundError()
    resume_id = thread.get("resume_id")
    entry_id = thread.get("profile_entry_id")
    if not isinstance(resume_id, ObjectId) or not isinstance(entry_id, str):
        raise ConflictError("The generation thread is missing its resume context.")

    resume_doc = await fetch_owned(Collections.RESUMES, str(resume_id), user)
    resume = Resume.model_validate(resume_doc)
    profile = await _load_profile_for_user(user)
    if profile.find_entry(entry_id) is None:
        raise ConflictError("The source profile entry no longer exists.")
    section = next(
        (
            item
            for item in resume.sections
            if item.visible
            and item.kind in {SectionKind.EXPERIENCE, SectionKind.PROJECTS}
            and entry_id in item.entry_ids
        ),
        None,
    )
    if section is None:
        raise ConflictError("The source profile entry is no longer included in this resume.")

    bullet_doc = await collection(Collections.BULLETS).find_one(
        {
            "_id": ObjectId(bullet_id),
            "user_id": user_oid,
            "thread_id": thread_id,
            "profile_entry_id": ObjectId(entry_id),
        }
    )
    if bullet_doc is None:
        raise NotFoundError()
    bullet = Bullet.model_validate(bullet_doc)
    if bullet.status is BulletStatus.ARCHIVED:
        raise ConflictError("This proposal has already been rejected.")

    if payload.action == "reject":
        section_bullets_path = f"sections.{resume.sections.index(section)}.bullet_ids"
        archived = await collection(Collections.BULLETS).update_one(
            {"_id": ObjectId(bullet_id), "user_id": user_oid},
            {
                "$set": {
                    "status": BulletStatus.ARCHIVED.value,
                    "review_decision": ProposalReviewDecision.REJECTED.value,
                    "updated_at": utcnow(),
                }
            },
        )
        if archived.matched_count == 0:
            raise ConflictError("The proposal changed; reload and try again.")
        if bullet_id in {str(item) for item in section.bullet_ids}:
            section.bullet_ids = [item for item in section.bullet_ids if str(item) != bullet_id]
            detached = await collection(Collections.RESUMES).update_one(
                {"_id": resume.id, "user_id": user_oid},
                {
                    "$pull": {section_bullets_path: ObjectId(bullet_id)},
                    "$set": {"updated_at": utcnow()},
                },
            )
            if detached.matched_count == 0:
                raise ConflictError("The resume changed; reload and try again.")
        return bullet.model_copy(update={"status": BulletStatus.ARCHIVED})

    if payload.action == "accept":
        if bullet.status is not BulletStatus.APPROVED:
            raise ConflictError("Revise this proposal into a compliant bullet before accepting it.")
        draft = bullet.as_draft()
    else:
        if payload.draft is None:
            raise ValidationFailedError("Provide the revised bullet components.")
        draft = payload.draft

    violations = draft.validate_xyz()
    if violations:
        raise ValidationFailedError(
            "The revised bullet must satisfy the XYZ quality checks.",
            details={
                "violations": [item.model_dump(mode="json", by_alias=True) for item in violations]
            },
        )

    updated_bullet = await collection(Collections.BULLETS).update_one(
        {"_id": ObjectId(bullet_id), "user_id": user_oid, "thread_id": thread_id},
        {
            "$set": {
                "action": draft.action,
                "metric": draft.metric,
                "method": draft.method,
                "skills": draft.skills,
                "status": BulletStatus.APPROVED.value,
                "review_decision": (
                    ProposalReviewDecision.ACCEPTED.value
                    if payload.action == "accept"
                    else ProposalReviewDecision.REVISED.value
                ),
                "violations": [],
                "updated_at": utcnow(),
            }
        },
    )
    if updated_bullet.matched_count == 0:
        raise ConflictError("The proposal changed; reload and try again.")
    if bullet_id not in {str(item) for item in section.bullet_ids}:
        section_bullets_path = f"sections.{resume.sections.index(section)}.bullet_ids"
        attached = await collection(Collections.RESUMES).update_one(
            {"_id": resume.id, "user_id": user_oid},
            {
                "$addToSet": {section_bullets_path: ObjectId(bullet_id)},
                "$set": {"updated_at": utcnow()},
            },
        )
        if attached.matched_count == 0:
            raise ConflictError("The resume changed; reload and try again.")
    updated = await collection(Collections.BULLETS).find_one(
        {"_id": ObjectId(bullet_id), "user_id": user_oid}
    )
    if updated is None:
        raise NotFoundError()
    return Bullet.model_validate(updated)


@router.get(
    "/{resume_id}/bullets",
    response_model=Page[Bullet],
    response_model_by_alias=False,
    summary="List bullets attached to a resume",
)
async def list_bullets(resume_id: str, user: CurrentUser, page: PaginationParams) -> Page[Bullet]:
    """Return attached bullets in resume section order, restricted to the owner."""
    resume_doc = await fetch_owned(Collections.RESUMES, resume_id, user)
    resume = Resume.model_validate(resume_doc)
    ordered_ids = [
        str(bullet_id)
        for section in sorted(resume.sections, key=lambda item: item.order)
        for bullet_id in section.bullet_ids
    ]
    unique_ids = list(dict.fromkeys(ordered_ids))
    if unique_ids:
        docs = (
            await collection(Collections.BULLETS)
            .find(
                {
                    "_id": {"$in": [ObjectId(bullet_id) for bullet_id in unique_ids]},
                    "user_id": ObjectId(str(user.id)),
                }
            )
            .to_list(length=len(unique_ids))
        )
        by_id = {str(doc["_id"]): Bullet.model_validate(doc) for doc in docs}
        items = [by_id[bullet_id] for bullet_id in unique_ids if bullet_id in by_id]
    else:
        items = []
    total = len(items)
    return Page[Bullet](
        items=items[page.offset : page.offset + page.limit],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


# ---------------------------------------------------------------------------
# Versions and rendering
# ---------------------------------------------------------------------------


@router.post(
    "/{resume_id}/render",
    response_model=RenderJob,
    response_model_by_alias=False,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Freeze a version and queue a PDF render",
)
async def render_resume(resume_id: str, payload: RenderRequest, user: CurrentUser) -> RenderJob:
    """Atomically snapshot a resume and enqueue its immutable render version."""
    if not ObjectId.is_valid(resume_id):
        raise NotFoundError()
    user_oid = ObjectId(str(user.id))
    resume_oid = ObjectId(resume_id)
    client = get_client()
    try:
        async with await client.start_session() as session, session.start_transaction():
            resume_doc = await collection(Collections.RESUMES).find_one(
                {"_id": resume_oid, "user_id": user_oid}, session=session
            )
            if resume_doc is None or resume_doc.get("status") == ResumeStatus.ARCHIVED.value:
                raise NotFoundError()
            resume = Resume.model_validate(resume_doc)
            profile = await _load_profile_for_user(user, session=session)
            bullets = await _load_approved_bullets(resume.sections, user, profile, session=session)

            version_number = resume.current_version + 1
            template = payload.template or resume.template
            version = _build_resume_snapshot(
                resume,
                profile,
                bullets,
                version_number=version_number,
                template=template,
                change_note=payload.change_note,
            )
            now = utcnow()
            incremented = await collection(Collections.RESUMES).update_one(
                {
                    "_id": resume_oid,
                    "user_id": user_oid,
                    "current_version": resume.current_version,
                    "status": {"$ne": ResumeStatus.ARCHIVED.value},
                },
                {"$inc": {"current_version": 1}, "$set": {"updated_at": now}},
                session=session,
            )
            if incremented.matched_count != 1:
                raise ConflictError("The resume changed while the render was starting.")

            inserted_version = await collection(Collections.RESUME_VERSIONS).insert_one(
                _version_to_mongo(version), session=session
            )
            version_id = inserted_version.inserted_id
            render_job = RenderJob(
                user_id=str(user_oid),
                resume_id=resume_id,
                version_id=str(version_id),
                status=RenderJobStatus.QUEUED,
            )
            inserted_job = await collection(Collections.RENDER_JOBS).insert_one(
                _render_job_to_mongo(render_job), session=session
            )
            render_job_id = inserted_job.inserted_id
    except NotFoundError:
        raise

    try:
        arq_job_id = await enqueue_render(
            version_id=str(version_id),
            user_id=str(user_oid),
            template=template.value,
        )
    except Exception as exc:  # noqa: BLE001 - persist failure state before returning
        await collection(Collections.RENDER_JOBS).update_one(
            {"_id": render_job_id, "user_id": user_oid},
            {
                "$set": {
                    "status": RenderJobStatus.FAILED.value,
                    "error_message": "The render could not be submitted to the worker queue.",
                    "finished_at": utcnow(),
                    "updated_at": utcnow(),
                }
            },
        )
        logger.error(
            "resume.render_enqueue_failed",
            resume_id=resume_id,
            error_type=type(exc).__name__,
        )
        raise QueueUnavailableError() from exc

    await collection(Collections.RENDER_JOBS).update_one(
        {"_id": render_job_id, "user_id": user_oid},
        {"$set": {"arq_job_id": arq_job_id, "updated_at": utcnow()}},
    )
    return render_job.model_copy(update={"id": str(render_job_id), "arq_job_id": arq_job_id})


@router.get(
    "/{resume_id}/versions",
    response_model=Page[ResumeVersion],
    response_model_by_alias=False,
    response_model_exclude={"items": {"__all__": {"latex_source"}}},
    summary="Version history",
)
async def list_versions(
    resume_id: str, user: CurrentUser, page: PaginationParams
) -> Page[ResumeVersion]:
    """Return immutable versions for this user's resume, newest first."""
    await fetch_owned(Collections.RESUMES, resume_id, user)
    query = {
        "resume_id": ObjectId(resume_id),
        "user_id": ObjectId(str(user.id)),
    }
    versions = collection(Collections.RESUME_VERSIONS)
    docs = await (
        versions.find(query, {"latex_source": 0})
        .sort("version", -1)
        .skip(page.offset)
        .limit(page.limit)
        .to_list(length=page.limit)
    )
    return Page[ResumeVersion](
        items=[ResumeVersion.model_validate(doc) for doc in docs],
        total=await versions.count_documents(query),
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/render-jobs/{job_id}",
    response_model=RenderJob,
    response_model_by_alias=False,
    summary="Poll a render job",
)
async def get_render_job(job_id: str, user: CurrentUser) -> RenderJob:
    """Return current render state and compiler diagnostics to its owner."""
    doc = await fetch_owned(Collections.RENDER_JOBS, job_id, user)
    return RenderJob.model_validate(doc)


def _resolve_pdf_file(pdf_path: str) -> Path:
    """Resolve a stored `pdf_path` under RENDER_STORAGE_DIR, refusing traversal."""
    root = get_settings().render_storage_dir.resolve()
    raw = Path(pdf_path)
    candidate = raw.resolve() if raw.is_absolute() else (root / pdf_path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        raise NotFoundError("Rendered PDF is not available.")
    return candidate


async def _owned_version_with_pdf(
    *,
    user: CurrentUser,
    resume_id: str,
    version_id: str | None = None,
    version_number: int | None = None,
) -> dict[str, object]:
    await fetch_owned(Collections.RESUMES, resume_id, user)
    query: dict[str, object] = {
        "resume_id": ObjectId(resume_id),
        "user_id": ObjectId(str(user.id)),
        "pdf_path": {"$nin": [None, ""]},
    }
    if version_id is not None:
        if not ObjectId.is_valid(version_id):
            raise NotFoundError()
        query["_id"] = ObjectId(version_id)
    if version_number is not None:
        query["version"] = version_number
    doc = await collection(Collections.RESUME_VERSIONS).find_one(
        query,
        sort=[("version", -1)],
    )
    if doc is None:
        raise NotFoundError("No rendered PDF for this resume.")
    return doc


@router.get(
    "/{resume_id}/pdf",
    summary="Stream the compiled PDF for the current (or requested) version",
    response_class=FileResponse,
)
async def stream_resume_pdf(
    resume_id: str,
    user: CurrentUser,
    v: int | None = Query(default=None, ge=1, description="Version number to stream."),
) -> FileResponse:
    """Ownership-checked PDF bytes for the Next.js same-origin iframe proxy."""
    doc = await _owned_version_with_pdf(
        user=user,
        resume_id=resume_id,
        version_number=v,
    )
    pdf_path = doc.get("pdf_path")
    if not isinstance(pdf_path, str):
        raise NotFoundError("Rendered PDF is not available.")
    path = _resolve_pdf_file(pdf_path)
    return FileResponse(
        path=path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )


@router.get(
    "/{resume_id}/versions/{version_id}/pdf",
    summary="Stream the compiled PDF for a specific version",
    response_class=FileResponse,
)
async def stream_version_pdf(resume_id: str, version_id: str, user: CurrentUser) -> FileResponse:
    doc = await _owned_version_with_pdf(
        user=user,
        resume_id=resume_id,
        version_id=version_id,
    )
    pdf_path = doc.get("pdf_path")
    if not isinstance(pdf_path, str):
        raise NotFoundError("Rendered PDF is not available.")
    path = _resolve_pdf_file(pdf_path)
    return FileResponse(
        path=path,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline"},
    )
