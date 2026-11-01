"""Resume routes: CRUD, the conversational generation agent, and rendering.

Real MongoDB-backed implementations. Two things worth reading before
extending this module:

1. Generation is *conversational and resumable*. The agent interrupts to ask
   for a missing metric, and the graph state is checkpointed to Mongo, so a
   user can close the tab and come back tomorrow. The endpoints below are
   therefore turn-based (`start`, then repeated `turn`) rather than one
   request/response. A missing LLM key surfaces as a clear upstream error
   from `app.agents.resume.llm` -- never as fabricated bullets.

2. Rendering is asynchronous and produces an immutable version. `POST
   /render` creates the `ResumeVersion` synchronously (cheap -- it snapshots
   data we already hold) and enqueues the LaTeX compile (expensive). The
   client polls the render job. If the queue is unavailable, the job is left
   QUEUED with the failure recorded on it rather than failing the snapshot:
   the version is real data, the compile is retryable work.

Ownership: every document is scoped to the caller. Single-document loads go
through `deps.fetch_owned`; list queries filter on `user_id` directly.
Documents store `user_id` (and foreign keys) as BSON ObjectIds, matching the
auth/profile convention, so `fetch_owned`'s ObjectId filter matches them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Query, status
from fastapi.responses import FileResponse
from langgraph.types import Command

from app.agents.checkpointer import thread_config
from app.agents.resume.graph import get_compiled_graph
from app.agents.resume.state import ResumeAgentState, initial_state
from app.api.deps import CurrentUser, PaginationParams, fetch_owned
from app.core.config import get_settings
from app.core.errors import NotFoundError, RateLimitedError, ValidationFailedError
from app.core.logging import get_logger
from app.db.mongo import Collections, collection
from app.models.agent import (
    AgentThread,
    BulletProposal,
    GenerateStartRequest,
    GenerateTurnRequest,
    GenerateTurnResponse,
    ThreadKind,
    ThreadStatus,
)
from app.models.base import Page, utcnow
from app.models.bullet import (
    Bullet,
    BulletDraft,
    BulletStatus,
    XYZViolation,
)
from app.models.profile import CareerProfile, ContactBlock
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
    ResumeUpdate,
    ResumeVersion,
    SectionKind,
)
from app.models.user import User
from app.services.queue import enqueue_render

router = APIRouter(prefix="/resumes", tags=["resumes"])
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _oid(user: User) -> ObjectId:
    """The caller's id as a BSON ObjectId, matching stored documents."""
    return ObjectId(str(user.id))


def _user_filter(user: User) -> dict[str, object]:
    """Match this user's documents whether `user_id` was stored as ObjectId or str.

    New documents store ObjectId (see module docstring); the `$in` keeps
    older string-stored rows readable instead of silently disappearing.
    """
    oid = _oid(user)
    return {"user_id": {"$in": [oid, str(oid)]}}


def _bson_safe(value: Any) -> Any:
    """Make a Pydantic dump legal for Motor (dates, enums, HttpUrl)."""
    if value is None or isinstance(value, (str, int, float, bool, bytes, ObjectId, datetime)):
        return value
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _bson_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bson_safe(item) for item in value]
    return str(value)


def _mongo_doc(model: Any, *, oid_fields: tuple[str, ...] = ()) -> dict[str, Any]:
    """Serialize a MongoModel for insertion, with ObjectId foreign keys."""
    doc = _bson_safe(model.to_mongo())
    for field in oid_fields:
        value = doc.get(field)
        if isinstance(value, str) and ObjectId.is_valid(value):
            doc[field] = ObjectId(value)
    if model.id and ObjectId.is_valid(str(model.id)):
        doc["_id"] = ObjectId(str(model.id))
    else:
        doc.pop("_id", None)
    return doc


def _doc_id(doc: dict[str, Any]) -> str:
    return str(doc["_id"])


async def _load_profile(user: User) -> CareerProfile | None:
    """The caller's career profile, or None when it has not been created yet."""
    doc = await collection(Collections.CAREER_PROFILES).find_one(_user_filter(user))
    return CareerProfile.model_validate(doc) if doc is not None else None


async def _load_resume_doc(resume_id: str, user: User) -> dict[str, Any]:
    return await fetch_owned(Collections.RESUMES, resume_id, user)


def _summary_from_doc(doc: dict[str, Any]) -> ResumeSummary:
    resume = Resume.model_validate(doc)
    return ResumeSummary(
        id=str(resume.id),
        title=resume.title,
        status=resume.status,
        template=resume.template,
        target_role=resume.target_role,
        current_version=resume.current_version,
        latest_ats_score=resume.latest_ats_score,
        updated_at=resume.updated_at,
    )


def _seed_sections(profile: CareerProfile | None) -> list[ResumeSection]:
    """Default sections in canonical ATS order, seeded from the profile.

    Users reorder from a sensible default far more often than they build from
    nothing, so experience/project/education entry ids are pre-filled and
    bullet selection is left empty for the editor/agent to fill.
    """
    entries: dict[SectionKind, list[str]] = {
        SectionKind.EXPERIENCE: [],
        SectionKind.PROJECTS: [],
        SectionKind.EDUCATION: [],
    }
    if profile is not None:
        entries[SectionKind.EXPERIENCE] = [e.entry_id for e in profile.experiences]
        entries[SectionKind.PROJECTS] = [p.entry_id for p in profile.projects]
        entries[SectionKind.EDUCATION] = [e.entry_id for e in profile.education]
    order = [
        (SectionKind.SUMMARY, "Summary"),
        (SectionKind.EXPERIENCE, "Experience"),
        (SectionKind.PROJECTS, "Projects"),
        (SectionKind.EDUCATION, "Education"),
        (SectionKind.SKILLS, "Skills"),
    ]
    sections = [
        ResumeSection(
            kind=kind,
            title=title,
            order=index,
            entry_ids=entries.get(kind, []),
        )
        for index, (kind, title) in enumerate(order)
    ]
    if profile is not None and profile.certifications:
        sections.append(
            ResumeSection(
                kind=SectionKind.CERTIFICATIONS,
                title="Certifications",
                order=len(sections),
                entry_ids=[c.entry_id for c in profile.certifications],
            )
        )
    return sections


def _bullet_from_doc(doc: dict[str, Any]) -> Bullet:
    """Validate a stored bullet, tolerating docs written before the fix.

    Bullets written before ``to_mongo`` excluded computed fields carry a
    stored ``text`` key, which ``Bullet.model_validate`` rejects under
    ``extra="forbid"`` (``text`` is a computed field, derived on read).
    Dropping it here -- rather than migrating every stored document -- is
    what keeps old bullets readable.
    """
    clean = {key: value for key, value in doc.items() if key != "text"}
    return Bullet.model_validate(clean)


async def _resume_thread_bullets(
    resume: Resume, user: User, *, assigned_ids: set[str]
) -> list[Bullet]:
    """Bullets generated for this resume that no section has claimed yet.

    Generation saves bullets against a thread (thread_id), not against a
    resume section -- see finalize.py -- so the section-only query in
    ``_ordered_bullets`` can never see them. Joining through agent_threads
    (whose resume_id is stored) surfaces them as the workspace's "Unassigned"
    pile, which is where the user assigns them to a section. Render still
    uses ``_ordered_bullets`` only, so an unassigned bullet can never sneak
    into a PDF before the user has placed it.
    """
    thread_docs = await collection(Collections.AGENT_THREADS).find(
        {"resume_id": ObjectId(str(resume.id)), **_user_filter(user)}
    ).to_list(length=1000)
    thread_ids = [doc["thread_id"] for doc in thread_docs if doc.get("thread_id")]
    if not thread_ids:
        return []
    cursor = (
        collection(Collections.BULLETS)
        .find(
            {"thread_id": {"$in": thread_ids}, **_user_filter(user)},
            sort=[("created_at", 1)],
        )
        .to_list(length=1000)
    )
    docs = await cursor
    return [
        _bullet_from_doc(doc)
        for doc in docs
        if doc.get("_id") is not None and str(doc["_id"]) not in assigned_ids
    ]


async def _ordered_bullets(
    resume: Resume, user: User
) -> tuple[list[Bullet], dict[str, Bullet]]:
    """Bullets for a resume, in section order.

    Mongo does not preserve `$in` ordering, so the order is restored in
    Python from the sections' `bullet_ids` -- that order is what the user
    arranged in the editor.
    """
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for section in resume.sections:
        for bullet_id in section.bullet_ids:
            key = str(bullet_id)
            if key not in seen:
                seen.add(key)
                ordered_ids.append(key)
    if not ordered_ids:
        return [], {}
    id_values: list[object] = []
    for bullet_id in ordered_ids:
        id_values.append(ObjectId(bullet_id) if ObjectId.is_valid(bullet_id) else bullet_id)
        id_values.append(bullet_id)
    cursor = collection(Collections.BULLETS).find(
        {"_id": {"$in": id_values}, **_user_filter(user)}
    )
    docs = await cursor.to_list(length=len(ordered_ids) * 2 + 10)
    by_id = {str(doc["_id"]): _bullet_from_doc(doc) for doc in docs}
    ordered = [by_id[bullet_id] for bullet_id in ordered_ids if bullet_id in by_id]
    return ordered, by_id


def _date_label(start: date | None, end: date | None) -> str:
    def fmt(value: date) -> str:
        return value.strftime("%b %Y")

    if start is None:
        return ""
    return f"{fmt(start)} - {fmt(end) if end is not None else 'Present'}"


def _ensure_entry(
    entries: dict[str, RenderedEntry],
    order: list[str],
    entry_meta: dict[str, tuple[str, str | None, str, str | None]],
    section_title: str,
    entry_id: str,
) -> RenderedEntry:
    """Get or create a rendered entry, preserving first-seen order."""
    if entry_id not in entries:
        heading, location, date_label, _ = entry_meta.get(
            entry_id, (section_title, None, "", None)
        )
        entries[entry_id] = RenderedEntry(
            entry_id=entry_id,
            heading=heading,
            subheading=location,
            date_label=date_label,
            location=location,
        )
        order.append(entry_id)
    return entries[entry_id]


def _rendered_sections(
    resume: Resume, bullets: list[Bullet], profile: CareerProfile | None
) -> list[RenderedSection]:
    """Freeze the resume's sections into immutable rendered sections.

    Profile entries supply headings and date labels; bullets are attached to
    the entry they belong to. A bullet whose entry is not in the section is
    attached to a section-level entry rather than dropped -- dropping user
    content at render time would be silent data loss.
    """
    by_bullet_id = {str(b.id): b for b in bullets}
    entry_meta: dict[str, tuple[str, str | None, str, str | None]] = {}
    if profile is not None:
        for entry in profile.experiences:
            entry_meta[entry.entry_id] = (
                f"{entry.title}, {entry.company}",
                entry.location,
                _date_label(entry.dates.start, entry.dates.end),
                None,
            )
        for project in profile.projects:
            entry_meta[project.entry_id] = (
                project.name,
                None,
                _date_label(
                    project.dates.start if project.dates else None,
                    project.dates.end if project.dates else None,
                ),
                None,
            )
        for edu in profile.education:
            entry_meta[edu.entry_id] = (
                edu.institution,
                None,
                _date_label(
                    edu.dates.start if edu.dates else None,
                    edu.dates.end if edu.dates else None,
                ),
                None,
            )

    rendered: list[RenderedSection] = []
    for section in resume.sections:
        entries: dict[str, RenderedEntry] = {}
        order: list[str] = []

        for bullet_id in section.bullet_ids:
            bullet = by_bullet_id.get(str(bullet_id))
            if bullet is None:
                continue
            entry = _ensure_entry(
                entries, order, entry_meta, section.title, str(bullet.profile_entry_id)
            )
            entry.bullets.append(
                RenderedBullet(
                    bullet_id=str(bullet.id),
                    text=bullet.text,
                    skills=bullet.skills,
                )
            )
        # Entries with no bullets still render as headings (e.g. education).
        for entry_id in section.entry_ids:
            _ensure_entry(entries, order, entry_meta, section.title, entry_id)
        rendered.append(
            RenderedSection(
                kind=section.kind,
                title=section.title,
                order=section.order,
                entries=[entries[entry_id] for entry_id in order],
            )
        )
    return rendered


def _proposals_from_state(state: dict[str, Any]) -> list[BulletProposal]:
    """Completed/flagged bullets from graph state, in the chat response shape."""
    proposals: list[BulletProposal] = []
    for key, accepted in (("completed_bullets", True), ("flagged_bullets", False)):
        for item in state.get(key, []) or []:
            if not isinstance(item, dict):
                continue
            draft_raw = item.get("draft")
            try:
                draft = (
                    draft_raw
                    if isinstance(draft_raw, BulletDraft)
                    else BulletDraft.model_validate(draft_raw)
                )
            except Exception as exc:  # noqa: BLE001 - a malformed draft must not kill the turn
                logger.warning("agent.malformed_draft_skipped", error=str(exc))
                continue
            violations = []
            for raw in item.get("violations", []) or []:
                try:
                    violations.append(
                        raw if isinstance(raw, XYZViolation) else XYZViolation.model_validate(raw)
                    )
                except Exception as exc:  # noqa: BLE001 - same reasoning as above
                    logger.warning("agent.malformed_violation_skipped", error=str(exc))
                    continue
            proposals.append(
                BulletProposal(
                    draft=draft,
                    text=item.get("text") or draft.text,
                    violations=violations,
                    repair_attempts=int(item.get("repair_attempts", 0)),
                    accepted=accepted,
                )
            )
    return proposals


def _response_from_state(state: dict[str, Any], thread_id: str) -> GenerateTurnResponse:
    """Translate raw graph state into the chat response contract.

    LangGraph 0.2 returns interrupted runs as state carrying `__interrupt__`
    rather than raising, so both that and the mine node's `pending_question`
    are checked. The last assistant message is the reply text; the question,
    when present, is what the UI should focus on.
    """
    question: str | None = None
    interrupts = state.get("__interrupt__") or []
    if interrupts:
        first = interrupts[0]
        value = getattr(first, "value", first)
        if isinstance(value, dict):
            question = value.get("question") or value.get("pending_question")
        elif isinstance(value, str):
            question = value
    if question is None:
        pending = state.get("pending_question")
        if isinstance(pending, str) and pending:
            question = pending

    messages = state.get("messages") or []
    assistant_message = ""
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str) and content:
                assistant_message = content
                break
    if not assistant_message:
        assistant_message = question or "Generation is in progress."

    if question:
        status = ThreadStatus.AWAITING_USER
    elif state.get("status") == "done":
        status = ThreadStatus.COMPLETED
    else:
        status = ThreadStatus.ACTIVE
    return GenerateTurnResponse(
        thread_id=thread_id,
        status=status,
        assistant_message=assistant_message,
        question=question,
        proposals=_proposals_from_state(state),
        needs_input=question is not None,
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=Page[ResumeSummary], summary="List resumes")
async def list_resumes(user: CurrentUser, page: PaginationParams) -> Page[ResumeSummary]:
    """Paginated resume list for the dashboard, newest first.

    Only summary fields are projected: `sections` can be tens of KB and is
    never shown in a list view. The `ix_user_updated` index covers this query.
    """
    filt = _user_filter(user)
    total = await collection(Collections.RESUMES).count_documents(filt)
    cursor = (
        collection(Collections.RESUMES)
        .find(filt, sort=[("updated_at", -1)])
        .skip(page.offset)
        .limit(page.limit)
    )
    docs = await cursor.to_list(length=page.limit)
    return Page[ResumeSummary](
        items=[_summary_from_doc(doc) for doc in docs],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("", response_model=Resume, status_code=status.HTTP_201_CREATED,
             summary="Create a resume")
async def create_resume(payload: ResumeCreate, user: CurrentUser) -> Resume:
    """Create a resume, seeded from the career profile when one exists.

    Sections start in canonical ATS order (summary, experience, projects,
    education, skills). Contact comes from the profile, falling back to the
    account's own name and email -- never invented placeholder data.
    """
    profile = await _load_profile(user)
    contact = (
        profile.contact
        if profile is not None
        else ContactBlock(full_name=user.full_name, email=str(user.email))
    )
    resume = Resume(
        user_id=str(user.id),
        title=payload.title,
        template=payload.template,
        target_job_id=payload.target_job_id,
        target_role=payload.target_role,
        contact=contact,
        sections=_seed_sections(profile),
    )
    doc = _mongo_doc(resume, oid_fields=("user_id", "target_job_id"))
    result = await collection(Collections.RESUMES).insert_one(doc)
    doc["_id"] = result.inserted_id
    return Resume.model_validate(doc)


@router.get("/{resume_id}", response_model=Resume, summary="Get a resume")
async def get_resume(resume_id: str, user: CurrentUser) -> Resume:
    """Fetch one resume with full sections, ownership-checked."""
    doc = await _load_resume_doc(resume_id, user)
    return Resume.model_validate(doc)


@router.patch("/{resume_id}", response_model=Resume, summary="Update a resume")
async def update_resume(resume_id: str, payload: ResumeUpdate, user: CurrentUser) -> Resume:
    """Partial update.

    Built from `payload.model_dump(exclude_unset=True)`: `exclude_unset`
    rather than `exclude_none`, so the caller can explicitly null out
    `summary`, which `exclude_none` would silently drop.
    """
    await _load_resume_doc(resume_id, user)
    updates = _bson_safe(payload.model_dump(exclude_unset=True, mode="json"))
    if updates:
        updates["updated_at"] = utcnow()
        await collection(Collections.RESUMES).update_one(
            {"_id": ObjectId(resume_id)}, {"$set": updates}
        )
    doc = await _load_resume_doc(resume_id, user)
    return Resume.model_validate(doc)


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Archive a resume")
async def delete_resume(resume_id: str, user: CurrentUser) -> None:
    """Soft-delete by setting status to ARCHIVED.

    Deliberately not a hard delete: `applications` reference `resume_versions`,
    and removing the resume behind a submitted application would leave the
    tracker unable to show what was actually sent.
    """
    await _load_resume_doc(resume_id, user)
    await collection(Collections.RESUMES).update_one(
        {"_id": ObjectId(resume_id)},
        {"$set": {"status": ResumeStatus.ARCHIVED.value, "updated_at": utcnow()}},
    )


# ---------------------------------------------------------------------------
# Conversational generation (LangGraph)
# ---------------------------------------------------------------------------


async def _check_generation_rate_limit(user: User) -> None:
    """Count this user's generation threads in the last hour against the cap.

    The cap exists because every turn spends LLM money; counting thread
    starts (rather than every message) is the conservative, auditable unit
    available without a dedicated rate-limit store.
    """
    settings = get_settings()
    since = utcnow() - timedelta(hours=1)
    count = await collection(Collections.AGENT_THREADS).count_documents(
        {**_user_filter(user), "created_at": {"$gte": since}}
    )
    if count >= settings.agent_rate_limit_per_hour:
        raise RateLimitedError(
            "Generation rate limit reached. Try again in an hour."
        )


def _entry_raw_notes(profile: CareerProfile | None, entry_id: str | None) -> str:
    if profile is None or entry_id is None:
        return ""
    for entry in (*profile.experiences, *profile.projects):
        if entry.entry_id == entry_id:
            return entry.raw_notes or ""
    return ""


async def _target_keywords(
    job_id: str | None, resume: Resume | None
) -> list[str]:
    target = job_id or (str(resume.target_job_id) if resume is not None else None)
    if target is None or not ObjectId.is_valid(target):
        return []
    doc = await collection(Collections.JOBS).find_one({"_id": ObjectId(target)})
    if doc is None:
        return []
    parsed = doc.get("parsed") or {}
    keywords = parsed.get("keywords") or []
    required = [k["term"] for k in keywords if isinstance(k, dict) and k.get("required")]
    preferred = [k["term"] for k in keywords if isinstance(k, dict) and not k.get("required")]
    return (required + preferred)[:20]


async def _update_thread_record(
    *, thread_id: str, user: User, response: GenerateTurnResponse
) -> None:
    await collection(Collections.AGENT_THREADS).update_one(
        {"thread_id": thread_id, **_user_filter(user)},
        {
            "$set": {
                "status": response.status.value,
                "bullets_produced": len(response.proposals),
                "last_message_at": utcnow(),
                "updated_at": utcnow(),
            },
            "$inc": {"message_count": 1},
        },
    )


@router.post(
    "/generate/start",
    response_model=GenerateTurnResponse,
    summary="Open a resume-generation conversation",
)
async def start_generation(
    payload: GenerateStartRequest, user: CurrentUser
) -> GenerateTurnResponse:
    """Begin a conversational generation session backed by the LangGraph agent.

    Mints a thread, persists an `AgentThread`, then invokes the compiled
    graph. The graph's first node mines `seed_notes` for candidate
    achievements and interrupts to ask about whichever one lacks a metric.
    A missing LLM key raises from `llm.py` -- the endpoint never substitutes
    fabricated bullets for a failed model call.
    """
    await _check_generation_rate_limit(user)
    resume: Resume | None = None
    if payload.resume_id is not None:
        resume = Resume.model_validate(await _load_resume_doc(str(payload.resume_id), user))
    profile = await _load_profile(user)
    seed_notes = (
        payload.seed_notes
        or _entry_raw_notes(profile, payload.profile_entry_id)
        or (resume.summary if resume is not None else None)
        or ""
    )
    thread_id = f"thread_{uuid.uuid4().hex[:24]}"
    thread = AgentThread(
        user_id=str(user.id),
        kind=ThreadKind.RESUME_GENERATION,
        thread_id=thread_id,
        resume_id=str(payload.resume_id) if payload.resume_id is not None else None,
        profile_entry_id=payload.profile_entry_id,
        status=ThreadStatus.ACTIVE,
        title=resume.title if resume is not None else "New conversation",
    )
    await collection(Collections.AGENT_THREADS).insert_one(
        _mongo_doc(thread, oid_fields=("user_id", "resume_id"))
    )
    if resume is not None:
        await collection(Collections.RESUMES).update_one(
            {"_id": ObjectId(str(resume.id))},
            {
                "$set": {
                    "thread_id": thread_id,
                    "status": ResumeStatus.GENERATING.value,
                    "updated_at": utcnow(),
                }
            },
        )

    graph = await get_compiled_graph()
    state: ResumeAgentState = await graph.ainvoke(
        initial_state(
            user_id=str(user.id),
            thread_id=thread_id,
            raw_notes=seed_notes,
            resume_id=str(payload.resume_id) if payload.resume_id is not None else None,
            profile_entry_id=payload.profile_entry_id,
            target_job_id=(
                str(payload.target_job_id)
                if payload.target_job_id is not None
                else (str(resume.target_job_id) if resume is not None else None)
            ),
            target_keywords=await _target_keywords(
                str(payload.target_job_id) if payload.target_job_id is not None else None,
                resume,
            ),
        ),
        config=thread_config(thread_id),
    )
    response = _response_from_state(dict(state), thread_id)
    await _update_thread_record(thread_id=thread_id, user=user, response=response)
    return response


@router.post(
    "/generate/{thread_id}/turn",
    response_model=GenerateTurnResponse,
    summary="Continue a generation conversation",
)
async def generation_turn(
    thread_id: str, payload: GenerateTurnRequest, user: CurrentUser
) -> GenerateTurnResponse:
    """Send one user message and resume the graph.

    Resumes via `graph.ainvoke(Command(resume=payload.message), ...)`. The
    checkpointer rehydrates state, so this handler stays stateless. A thread
    that does not belong to the caller is indistinguishable from a missing
    one, by the same ownership rule as every other endpoint here.
    """
    thread_doc = await collection(Collections.AGENT_THREADS).find_one(
        {"thread_id": thread_id, **_user_filter(user)}
    )
    if thread_doc is None:
        raise NotFoundError()
    thread = AgentThread.model_validate(thread_doc)
    if thread.status is ThreadStatus.COMPLETED:
        return GenerateTurnResponse(
            thread_id=thread_id,
            status=ThreadStatus.COMPLETED,
            assistant_message="This conversation is already complete.",
            needs_input=False,
        )
    graph = await get_compiled_graph()
    state: ResumeAgentState = await graph.ainvoke(
        Command(resume=payload.message),
        config=thread_config(thread_id),
    )
    response = _response_from_state(dict(state), thread_id)
    await _update_thread_record(thread_id=thread_id, user=user, response=response)
    return response


@router.get(
    "/{resume_id}/bullets",
    response_model=Page[Bullet],
    summary="List bullets attached to a resume",
)
async def list_bullets(
    resume_id: str, user: CurrentUser, page: PaginationParams
) -> Page[Bullet]:
    """Bullets for the editor pane: section bullets first, then unassigned.

    Section bullets come in the resume's section order; unassigned bullets
    (generated for this resume, not yet placed in a section) follow in
    creation order. The workspace page splits the two by section membership,
    so returning only section bullets -- the old behaviour -- made generated
    bullets invisible even though they were saved. Rendering deliberately
    does NOT use this endpoint (see _ordered_bullets), so unassigned bullets
    still cannot reach a PDF until assigned.
    """
    resume = Resume.model_validate(await _load_resume_doc(resume_id, user))
    ordered, _ = await _ordered_bullets(resume, user)
    assigned_ids = {str(b.id) for b in ordered if b.id is not None}
    unassigned = await _resume_thread_bullets(resume, user, assigned_ids=assigned_ids)
    all_bullets = ordered + unassigned
    window = all_bullets[page.offset : page.offset + page.limit]
    return Page[Bullet](
        items=window,
        total=len(all_bullets),
        limit=page.limit,
        offset=page.offset,
    )


# ---------------------------------------------------------------------------
# Versions and rendering
# ---------------------------------------------------------------------------


@router.post(
    "/{resume_id}/render",
    response_model=RenderJob,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Freeze a version and queue a PDF render",
)
async def render_resume(
    resume_id: str, payload: RenderRequest, user: CurrentUser
) -> RenderJob:
    """Snapshot the resume into an immutable version and queue the compile.

    202 rather than 200 because the PDF does not exist yet -- the client polls
    the returned render job.

    Order of operations, and why:
      1. Load resume + referenced bullets, assert every bullet is APPROVED.
         Refuse the render otherwise: a PDF containing a bullet that failed XYZ
         validation is precisely the outcome this product exists to prevent.
      2. Build the `ResumeVersion` snapshot, denormalizing bullet text.
      3. Insert it, `$inc` the resume's `current_version`.
      4. Insert the `RenderJob`, then `enqueue_render(...)` and record the
         returned arq job id. A queue failure leaves the job QUEUED with the
         error recorded, rather than discarding the snapshot.
    """
    resume = Resume.model_validate(await _load_resume_doc(resume_id, user))
    bullets, _ = await _ordered_bullets(resume, user)
    unapproved = [b for b in bullets if b.status is not BulletStatus.APPROVED]
    if unapproved:
        raise ValidationFailedError(
            "All bullets must be APPROVED before rendering. "
            f"{len(unapproved)} bullet(s) are not approved."
        )
    profile = await _load_profile(user)
    template = payload.template or resume.template
    version = ResumeVersion(
        resume_id=str(resume.id),
        user_id=str(user.id),
        version=resume.current_version + 1,
        template=template,
        contact=resume.contact,
        summary=resume.summary,
        sections=_rendered_sections(resume, bullets, profile),
        skills=profile.skills if profile is not None else [],
        education=profile.education if profile is not None else [],
        certifications=profile.certifications if profile is not None else [],
        change_note=payload.change_note,
    )
    version_doc = _mongo_doc(
        version, oid_fields=("resume_id", "user_id")
    )
    version_result = await collection(Collections.RESUME_VERSIONS).insert_one(version_doc)
    version_doc["_id"] = version_result.inserted_id
    await collection(Collections.RESUMES).update_one(
        {"_id": ObjectId(str(resume.id))},
        {
            "$inc": {"current_version": 1},
            "$set": {"status": ResumeStatus.READY.value, "updated_at": utcnow()},
        },
    )
    job = RenderJob(
        user_id=str(user.id),
        resume_id=str(resume.id),
        version_id=str(version_result.inserted_id),
        status=RenderJobStatus.QUEUED,
    )
    job_doc = _mongo_doc(job, oid_fields=("user_id", "resume_id", "version_id"))
    job_result = await collection(Collections.RENDER_JOBS).insert_one(job_doc)
    job_doc["_id"] = job_result.inserted_id
    try:
        arq_job_id = await enqueue_render(
            version_id=str(version_result.inserted_id),
            user_id=str(user.id),
            template=template.value,
        )
    except Exception as exc:  # noqa: BLE001 - queue outage must not discard the snapshot
        await collection(Collections.RENDER_JOBS).update_one(
            {"_id": job_result.inserted_id},
            {"$set": {"error_message": f"enqueue failed: {exc}"}},
        )
        job_doc["error_message"] = f"enqueue failed: {exc}"
    else:
        if arq_job_id is not None:
            job_doc["arq_job_id"] = arq_job_id
            await collection(Collections.RENDER_JOBS).update_one(
                {"_id": job_result.inserted_id}, {"$set": {"arq_job_id": arq_job_id}}
            )
    return RenderJob.model_validate(job_doc)


@router.get(
    "/{resume_id}/versions",
    response_model=Page[ResumeVersion],
    summary="Version history",
)
async def list_versions(
    resume_id: str, user: CurrentUser, page: PaginationParams
) -> Page[ResumeVersion]:
    """Immutable version history, newest first.

    Covered by `uq_resume_version` on `(resume_id, version DESC)`.
    `latex_source` is excluded from the projection -- it is large and only
    needed when debugging a specific compile.
    """
    await _load_resume_doc(resume_id, user)
    filt = {"resume_id": ObjectId(resume_id)}
    total = await collection(Collections.RESUME_VERSIONS).count_documents(filt)
    cursor = (
        collection(Collections.RESUME_VERSIONS)
        .find(filt, {"latex_source": 0}, sort=[("version", -1)])
        .skip(page.offset)
        .limit(page.limit)
    )
    docs = await cursor.to_list(length=page.limit)
    return Page[ResumeVersion](
        items=[ResumeVersion.model_validate(doc) for doc in docs],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/render-jobs/{job_id}",
    response_model=RenderJob,
    summary="Poll a render job",
)
async def get_render_job(job_id: str, user: CurrentUser) -> RenderJob:
    """Poll compile status, including `latex_log_tail` on failure.

    pdflatex errors are opaque enough that the log is the only useful signal,
    and surfacing it turns a support ticket into a self-service fix.
    """
    if not ObjectId.is_valid(job_id):
        raise NotFoundError()
    doc = await collection(Collections.RENDER_JOBS).find_one(
        {"_id": ObjectId(job_id), **_user_filter(user)}
    )
    if doc is None:
        raise NotFoundError()
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
        user=user, resume_id=resume_id, version_number=v,
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
async def stream_version_pdf(
    resume_id: str, version_id: str, user: CurrentUser
) -> FileResponse:
    doc = await _owned_version_with_pdf(
        user=user, resume_id=resume_id, version_id=version_id,
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
