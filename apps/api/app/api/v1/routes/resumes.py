"""Resume routes: CRUD, the conversational generation agent, and rendering.

STUB: handlers return realistic mock data so the frontend can be built
against a live server today. Each carries a TODO naming the specific
integration work and the non-obvious constraints to respect.

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

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, PaginationParams
from app.models.agent import (
    BulletProposal,
    GenerateStartRequest,
    GenerateTurnRequest,
    GenerateTurnResponse,
    ThreadStatus,
)
from app.models.base import Page, utcnow
from app.models.bullet import (
    Bullet,
    BulletDraft,
    BulletStatus,
    XYZViolation,
    XYZViolationCode,
)
from app.models.profile import ContactBlock
from app.models.resume import (
    RenderJob,
    RenderJobStatus,
    RenderRequest,
    Resume,
    ResumeCreate,
    ResumeStatus,
    ResumeSummary,
    ResumeTemplate,
    ResumeUpdate,
    ResumeVersion,
)

router = APIRouter(prefix="/resumes", tags=["resumes"])

_MOCK_ID = "000000000000000000000000"
_MOCK_CONTACT = ContactBlock(
    full_name="Ada Lovelace",
    email="ada@example.com",
    location="London, UK",
)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("", response_model=Page[ResumeSummary], summary="List resumes")
async def list_resumes(user: CurrentUser, page: PaginationParams) -> Page[ResumeSummary]:
    """Paginated resume list for the dashboard.

    TODO: query `resumes` filtered by user_id, sorted by updated_at DESC --
    the `ix_user_updated` index covers exactly this. Project only the
    ResumeSummary fields; `sections` can be tens of KB and is never shown in a
    list view.
    """
    return Page[ResumeSummary](
        items=[
            ResumeSummary(
                id=_MOCK_ID,
                title="Backend Engineer - Stripe",
                status=ResumeStatus.READY,
                template=ResumeTemplate.ATS_CLASSIC,
                target_role="Senior Backend Engineer",
                current_version=3,
                latest_ats_score=87.5,
                updated_at=utcnow(),
            )
        ],
        total=1,
        limit=page.limit,
        offset=page.offset,
    )


@router.post("", response_model=Resume, status_code=status.HTTP_201_CREATED,
             summary="Create a resume")
async def create_resume(payload: ResumeCreate, user: CurrentUser) -> Resume:
    """Create an empty resume.

    TODO: seed `sections` from the user's CareerProfile in canonical ATS order
    (summary, experience, projects, education, skills). Users reorder from a
    sensible default far more often than they build from nothing.
    """
    return Resume(
        user_id=str(user.id or _MOCK_ID),
        title=payload.title,
        template=payload.template,
        target_job_id=payload.target_job_id,
        target_role=payload.target_role,
        contact=_MOCK_CONTACT,
    )


@router.get("/{resume_id}", response_model=Resume, summary="Get a resume")
async def get_resume(resume_id: str, user: CurrentUser) -> Resume:
    """Fetch one resume with full sections.

    TODO: use `deps.fetch_owned(Collections.RESUMES, resume_id, user)` -- it
    folds the ownership check into the query filter.
    """
    return Resume(
        id=resume_id,
        user_id=str(user.id or _MOCK_ID),
        title="Backend Engineer - Stripe",
        status=ResumeStatus.READY,
        contact=_MOCK_CONTACT,
        current_version=3,
    )


@router.patch("/{resume_id}", response_model=Resume, summary="Update a resume")
async def update_resume(resume_id: str, payload: ResumeUpdate, user: CurrentUser) -> Resume:
    """Partial update.

    TODO: build the `$set` from `payload.model_dump(exclude_unset=True)`.
    `exclude_unset` rather than `exclude_none` -- the caller must be able to
    explicitly null out `summary`, which `exclude_none` would silently drop.
    """
    return Resume(
        id=resume_id,
        user_id=str(user.id or _MOCK_ID),
        title=payload.title or "Backend Engineer - Stripe",
        status=payload.status or ResumeStatus.DRAFT,
        contact=_MOCK_CONTACT,
    )


@router.delete("/{resume_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Archive a resume")
async def delete_resume(resume_id: str, user: CurrentUser) -> None:
    """Soft-delete by setting status to ARCHIVED.

    Deliberately not a hard delete: `applications` reference `resume_versions`,
    and removing the resume behind a submitted application would leave the
    tracker unable to show what was actually sent.
    """
    return None


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
    """Begin a conversational generation session.

    TODO: mint a thread_id, persist an `AgentThread`, then invoke the compiled
    graph from `app.agents.resume.graph`. The graph's first node mines
    `seed_notes` for candidate achievements and immediately interrupts to ask
    about whichever one lacks a metric.

    Rate-limit this against `settings.agent_rate_limit_per_hour`: it is the
    only endpoint that spends money per call.
    """
    return GenerateTurnResponse(
        thread_id="thread_01H8XGJWBWBAQ4T2NQ9BM8ZZZZ",
        status=ThreadStatus.AWAITING_USER,
        assistant_message=(
            "I found three achievements in your notes. Let's make each one measurable. "
            "You mentioned rebuilding the payments retry logic -- what changed as a result?"
        ),
        question="What measurable outcome did the retry rebuild produce?",
        needs_input=True,
    )


@router.post(
    "/generate/{thread_id}/turn",
    response_model=GenerateTurnResponse,
    summary="Continue a generation conversation",
)
async def generation_turn(
    thread_id: str, payload: GenerateTurnRequest, user: CurrentUser
) -> GenerateTurnResponse:
    """Send one user message and resume the graph.

    TODO: resume via `graph.ainvoke(Command(resume=payload.message),
    config={"configurable": {"thread_id": thread_id}})`. The checkpointer
    rehydrates state, so this handler stays stateless.

    Note the shape of the mock: a bullet that needed one repair pass, with the
    violation retained. Showing the user *why* it was repaired is what teaches
    them to write better raw notes next time.
    """
    draft = BulletDraft(
        action="Rebuilt the payment retry pipeline",
        metric="cutting failed-charge rate from 4.2% to 0.8% across 1.2M monthly transactions",
        method="using idempotency keys and exponential backoff in a Redis-backed job queue",
        skills=["Python", "Redis", "Distributed Systems", "Payments"],
    )
    return GenerateTurnResponse(
        thread_id=thread_id,
        status=ThreadStatus.ACTIVE,
        assistant_message=(
            "That works. Here's the bullet -- the first draft said 'improved reliability', "
            "which has no number in it, so I asked for the rate change."
        ),
        proposals=[
            BulletProposal(
                draft=draft,
                text=draft.text,
                violations=[
                    XYZViolation(
                        code=XYZViolationCode.MISSING_METRIC,
                        message="First draft had no quantity in the Y component.",
                        hint="Repaired after you supplied the failure-rate change.",
                    )
                ],
                repair_attempts=1,
                accepted=True,
            )
        ],
        needs_input=False,
    )


@router.get(
    "/{resume_id}/bullets",
    response_model=Page[Bullet],
    summary="List bullets attached to a resume",
)
async def list_bullets(
    resume_id: str, user: CurrentUser, page: PaginationParams
) -> Page[Bullet]:
    """Bullets for the editor pane.

    TODO: gather bullet_ids across the resume's sections and fetch with a
    single `$in` query, then re-sort in Python to match section order --
    Mongo does not preserve `$in` ordering.
    """
    return Page[Bullet](
        items=[
            Bullet(
                id=_MOCK_ID,
                user_id=str(user.id or _MOCK_ID),
                profile_entry_id=_MOCK_ID,
                action="Rebuilt the payment retry pipeline",
                metric="cutting failed-charge rate from 4.2% to 0.8% across 1.2M transactions",
                method="using idempotency keys and exponential backoff in a Redis job queue",
                skills=["Python", "Redis", "Payments"],
                status=BulletStatus.APPROVED,
            )
        ],
        total=1,
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

    TODO, in this order:
      1. Load resume + referenced bullets, assert every bullet is APPROVED.
         Refuse the render otherwise: a PDF containing a bullet that failed XYZ
         validation is precisely the outcome this product exists to prevent.
      2. Build the `ResumeVersion` snapshot, denormalizing bullet text.
      3. Insert it, `$inc` the resume's `current_version`.
      4. `enqueue_render(...)` and record the returned arq_job_id.

    Steps 2-3 belong in one transaction, hence the replica-set requirement in
    docker-compose.
    """
    return RenderJob(
        user_id=str(user.id or _MOCK_ID),
        resume_id=resume_id,
        version_id=_MOCK_ID,
        arq_job_id="render:000000000000000000000000",
        status=RenderJobStatus.QUEUED,
    )


@router.get(
    "/{resume_id}/versions",
    response_model=Page[ResumeVersion],
    summary="Version history",
)
async def list_versions(
    resume_id: str, user: CurrentUser, page: PaginationParams
) -> Page[ResumeVersion]:
    """Immutable version history, newest first.

    TODO: covered by `uq_resume_version` on `(resume_id, version DESC)`.
    Exclude `latex_source` from the projection -- it is large and only needed
    when debugging a specific compile.
    """
    return Page[ResumeVersion](
        items=[
            ResumeVersion(
                id=_MOCK_ID,
                resume_id=resume_id,
                user_id=str(user.id or _MOCK_ID),
                version=3,
                template=ResumeTemplate.ATS_CLASSIC,
                contact=_MOCK_CONTACT,
                pdf_path="storage/renders/000000000000000000000000.pdf",
                page_count=1,
                rendered_at=utcnow(),
                change_note="Tailored bullets to the Stripe posting's keyword set.",
            )
        ],
        total=1,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/render-jobs/{job_id}",
    response_model=RenderJob,
    summary="Poll a render job",
)
async def get_render_job(job_id: str, user: CurrentUser) -> RenderJob:
    """Poll compile status.

    TODO: read from `render_jobs`. On failure, return `latex_log_tail` --
    pdflatex errors are opaque enough that the log is the only useful signal,
    and surfacing it turns a support ticket into a self-service fix.
    """
    return RenderJob(
        id=job_id,
        user_id=str(user.id or _MOCK_ID),
        resume_id=_MOCK_ID,
        version_id=_MOCK_ID,
        status=RenderJobStatus.SUCCEEDED,
        duration_ms=2840,
        finished_at=utcnow(),
    )
