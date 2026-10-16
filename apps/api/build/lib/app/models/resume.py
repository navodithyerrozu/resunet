"""Resume documents and their immutable rendered versions.

Two collections, and the split is deliberate:

`resumes` is the *mutable* logical document -- title, target job, section
ordering, and which bullets are currently selected. It changes as the user
edits.

`resume_versions` are *immutable snapshots*. Every render produces one, and
it denormalizes the full bullet text at the moment of rendering. This costs
duplicated text and buys something more valuable: when a user asks "what
exactly did I send to Stripe in March," the answer is byte-stable forever.
If versions referenced live bullets by id, editing a bullet today would
silently rewrite history, and the PDF on disk would no longer match the
record describing it.

Applications point at a *version*, never at a resume.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from app.models.base import ApiModel, AppModel, MongoModel, PyObjectId
from app.models.profile import (
    Certification,
    ContactBlock,
    EducationEntry,
    Skill,
)


class ResumeStatus(StrEnum):
    DRAFT = "draft"
    GENERATING = "generating"      # agent run in flight
    READY = "ready"                # bullets selected, renderable
    ARCHIVED = "archived"


class ResumeTemplate(StrEnum):
    """LaTeX templates in apps/render-worker/worker/templates.

    All are single-column by design. Two-column layouts read in the wrong
    order when an ATS linearizes the text, which silently scrambles a resume
    that looks fine to a human.
    """

    ATS_CLASSIC = "ats_classic"
    ATS_COMPACT = "ats_compact"
    ATS_TECHNICAL = "ats_technical"


class SectionKind(StrEnum):
    SUMMARY = "summary"
    EXPERIENCE = "experience"
    PROJECTS = "projects"
    EDUCATION = "education"
    SKILLS = "skills"
    CERTIFICATIONS = "certifications"


class ResumeSection(AppModel):
    """One section's ordering and inclusion rules.

    Bullet *selection* lives here rather than on the bullet because the same
    bullet appears in many resumes with different ordering; membership is a
    property of the resume, not of the bullet.
    """

    kind: SectionKind
    title: str = Field(description="Display heading, e.g. 'Experience'.")
    order: int = Field(ge=0)
    visible: bool = True
    entry_ids: list[str] = Field(
        default_factory=list,
        description="Profile entry ids to include, in render order.",
    )
    bullet_ids: list[PyObjectId] = Field(
        default_factory=list,
        description="Selected bullets, in render order.",
    )


class Resume(MongoModel):
    """Collection: `resumes`. The mutable working document."""

    user_id: PyObjectId
    title: str = Field(min_length=1, max_length=160, description="User-facing label.")
    status: ResumeStatus = ResumeStatus.DRAFT
    template: ResumeTemplate = ResumeTemplate.ATS_CLASSIC

    target_job_id: PyObjectId | None = Field(
        default=None, description="Job this resume is tailored to, if any."
    )
    target_role: str | None = Field(
        default=None, description="Free-text target when no job posting is attached."
    )

    contact: ContactBlock
    summary: str | None = Field(default=None, max_length=1200)
    sections: list[ResumeSection] = Field(default_factory=list)

    thread_id: str | None = Field(
        default=None,
        description="LangGraph thread for the conversational session that built this "
        "resume. Persisted so the user can resume a half-finished conversation days later.",
    )
    current_version: int = Field(default=0, ge=0)
    latest_ats_score: float | None = Field(
        default=None, ge=0, le=100,
        description="Denormalized from the newest ATSScore so list views don't need a join.",
    )


class RenderedBullet(AppModel):
    """A bullet frozen at render time."""

    bullet_id: PyObjectId
    text: str = Field(description="Assembled XYZ text as rendered. Never edited in place.")
    skills: list[str] = Field(default_factory=list)


class RenderedEntry(AppModel):
    """A profile entry frozen at render time."""

    entry_id: str
    heading: str = Field(description="e.g. 'Senior Engineer, Acme'.")
    subheading: str | None = None
    date_label: str = Field(description="Pre-formatted, e.g. 'Jan 2023 - Present'.")
    location: str | None = None
    bullets: list[RenderedBullet] = Field(default_factory=list)


class RenderedSection(AppModel):
    kind: SectionKind
    title: str
    order: int
    entries: list[RenderedEntry] = Field(default_factory=list)


class ResumeVersion(MongoModel):
    """Collection: `resume_versions`. Immutable snapshot.

    Written once and never updated. `(resume_id, version)` is unique.
    """

    resume_id: PyObjectId
    user_id: PyObjectId
    version: int = Field(ge=1)

    template: ResumeTemplate
    contact: ContactBlock
    summary: str | None = None
    sections: list[RenderedSection] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)

    pdf_path: str | None = Field(
        default=None, description="Storage path once the render worker finishes."
    )
    pdf_sha256: str | None = Field(
        default=None, description="Content hash; lets identical renders dedupe."
    )
    latex_source: str | None = Field(
        default=None, description="Exact .tex submitted to pdflatex, retained for debugging."
    )
    page_count: int | None = Field(default=None, ge=1)
    rendered_at: datetime | None = None

    change_note: str | None = Field(
        default=None, max_length=500,
        description="What changed versus the previous version.",
    )


# --- API contracts ---------------------------------------------------------


class ResumeCreate(ApiModel):
    title: str = Field(min_length=1, max_length=160)
    template: ResumeTemplate = ResumeTemplate.ATS_CLASSIC
    target_job_id: PyObjectId | None = None
    target_role: str | None = None


class ResumeUpdate(ApiModel):
    """PATCH body. Every field optional; unset fields are left alone."""

    title: str | None = Field(default=None, min_length=1, max_length=160)
    status: ResumeStatus | None = None
    template: ResumeTemplate | None = None
    summary: str | None = Field(default=None, max_length=1200)
    sections: list[ResumeSection] | None = None


class ResumeSummary(ApiModel):
    """List-view projection -- deliberately omits sections, which dominate payload size."""

    id: PyObjectId
    title: str
    status: ResumeStatus
    template: ResumeTemplate
    target_role: str | None
    current_version: int
    latest_ats_score: float | None
    updated_at: datetime


class RenderRequest(ApiModel):
    template: ResumeTemplate | None = Field(
        default=None, description="Overrides the resume's stored template for this render."
    )
    change_note: str | None = Field(default=None, max_length=500)


class RenderJobStatus(StrEnum):
    QUEUED = "queued"
    RENDERING = "rendering"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RenderJob(MongoModel):
    """Collection: `render_jobs`. Tracks an async LaTeX compile.

    Exists so the UI can poll for progress and so LaTeX failures are
    inspectable after the fact -- pdflatex errors are cryptic and you will
    want the log.
    """

    user_id: PyObjectId
    resume_id: PyObjectId
    version_id: PyObjectId
    arq_job_id: str | None = None

    status: RenderJobStatus = RenderJobStatus.QUEUED
    attempts: int = Field(default=0, ge=0)
    error_message: str | None = None
    latex_log_tail: str | None = Field(
        default=None, description="Last ~2KB of the pdflatex log when a compile fails."
    )
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
