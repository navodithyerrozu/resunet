"""Career profile: the user's master record of everything they have done.

One document per user. Sub-entries are *embedded* rather than split into
their own collections because they are always read together (you never load
one job without the rest of the resume), they are naturally bounded (a few
dozen entries over a career), and they are edited as a unit. Bullets are the
one exception -- they live in their own collection because they are reused
across resumes and scored independently.

Each embedded entry carries a stable `entry_id` so bullets can reference it
without depending on array position, which reorders whenever the user
rearranges their history.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import TypeVar

from bson import ObjectId
from pydantic import Field, HttpUrl, model_validator

from app.models.base import ApiModel, AppModel, MongoModel, PyObjectId

_EntryT = TypeVar("_EntryT", bound=AppModel)

# Equal-weight onboarding checklist. Projects and certifications are optional
# extras and do not move the bar -- a complete first job + education + skills
# should already show a full progress meter.
_COMPLETENESS_NOTE_MIN_CHARS = 20


def _new_entry_id() -> str:
    """Stable identifier for an embedded entry."""
    return str(ObjectId())


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    INTERNSHIP = "internship"
    FREELANCE = "freelance"
    VOLUNTEER = "volunteer"


class SkillProficiency(StrEnum):
    FAMILIAR = "familiar"
    PROFICIENT = "proficient"
    ADVANCED = "advanced"
    EXPERT = "expert"


class DateRange(AppModel):
    """A start/end span where an absent end means 'present'."""

    start: date
    end: date | None = Field(
        default=None, description="None means currently ongoing."
    )

    @model_validator(mode="after")
    def _check_order(self) -> DateRange:
        if self.end and self.end < self.start:
            raise ValueError("end date precedes start date")
        return self

    @property
    def is_current(self) -> bool:
        return self.end is None


class ExperienceEntry(AppModel):
    """A job. Bullets attach to this via `Bullet.profile_entry_id`."""

    entry_id: str = Field(default_factory=_new_entry_id)
    company: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=160)
    location: str | None = None
    employment_type: EmploymentType = EmploymentType.FULL_TIME
    dates: DateRange
    tech_stack: list[str] = Field(default_factory=list, max_length=40)

    raw_notes: str = Field(
        default="",
        max_length=8000,
        description="The user's unstructured brain-dump about this role. This is the "
        "primary input to the resume agent -- it mines raw notes for candidate "
        "achievements, then presses for the missing metric on each one.",
    )


class ProjectEntry(AppModel):
    """Side project or portfolio piece. Also a valid bullet parent."""

    entry_id: str = Field(default_factory=_new_entry_id)
    name: str = Field(min_length=1, max_length=160)
    role: str | None = None
    dates: DateRange | None = None
    url: HttpUrl | None = None
    repo_url: HttpUrl | None = None
    tech_stack: list[str] = Field(default_factory=list, max_length=40)
    raw_notes: str = Field(default="", max_length=4000)


class EducationEntry(AppModel):
    entry_id: str = Field(default_factory=_new_entry_id)
    institution: str = Field(min_length=1, max_length=160)
    degree: str | None = None
    field_of_study: str | None = None
    dates: DateRange | None = None
    gpa: float | None = Field(default=None, ge=0, le=10)
    honors: list[str] = Field(default_factory=list)
    coursework: list[str] = Field(default_factory=list, max_length=20)


class SkillCount(ApiModel):
    """One evidenced skill and how many XYZ-compliant bullets carry it."""

    name: str = Field(min_length=1, max_length=80)
    count: int = Field(ge=0)


class Skill(AppModel):
    name: str = Field(min_length=1, max_length=80)
    category: str | None = Field(
        default=None, description="e.g. 'Languages', 'Cloud', 'ML' -- drives resume grouping."
    )
    proficiency: SkillProficiency = SkillProficiency.PROFICIENT
    years: float | None = Field(default=None, ge=0, le=60)


class Certification(AppModel):
    entry_id: str = Field(default_factory=_new_entry_id)
    name: str
    issuer: str | None = None
    issued_on: date | None = None
    expires_on: date | None = None
    credential_url: HttpUrl | None = None


class ContactBlock(AppModel):
    """Header block of the rendered resume.

    Kept here rather than on `User` because the resume header is presentation
    data the user tunes per audience (some omit a phone number), while
    `User.email` is an identity key.
    """

    full_name: str
    email: str
    phone: str | None = None
    location: str | None = None
    linkedin_url: HttpUrl | None = None
    github_url: HttpUrl | None = None
    portfolio_url: HttpUrl | None = None


class CareerProfile(MongoModel):
    """Collection: `career_profiles`. Exactly one per user (unique index)."""

    user_id: PyObjectId
    contact: ContactBlock
    headline: str | None = Field(
        default=None, max_length=200, description="e.g. 'Backend engineer, distributed systems'."
    )
    summary: str | None = Field(default=None, max_length=1200)

    experiences: list[ExperienceEntry] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    skills: list[Skill] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)

    completeness: float = Field(
        default=0.0, ge=0, le=1,
        description="Cached 0-1 fraction driving the onboarding progress UI. "
        "Recomputed on write, not on read.",
    )

    def find_entry(self, entry_id: str) -> ExperienceEntry | ProjectEntry | None:
        """Resolve a bullet's parent entry across both bullet-bearing lists."""
        for entry in (*self.experiences, *self.projects):
            if entry.entry_id == entry_id:
                return entry
        return None

    def bullet_parent_ids(self) -> set[str]:
        """Experience and project ids that bullets may legally reference."""
        return {entry.entry_id for entry in (*self.experiences, *self.projects)}

    def compute_completeness(self) -> float:
        """Onboarding progress in [0, 1]. Always derived server-side.

        Seven equally weighted checks:

        1. Contact identity (name + email) -- set at registration.
        2. Reachability (phone, location, or LinkedIn).
        3. Positioning (headline or summary).
        4. At least one job.
        5. At least one job with usable ``raw_notes`` (the agent's source text).
        6. At least one education entry.
        7. At least one skill.
        """
        checks = (
            bool(self.contact.full_name.strip() and self.contact.email.strip()),
            bool(
                (self.contact.phone or "").strip()
                or (self.contact.location or "").strip()
                or self.contact.linkedin_url
            ),
            bool((self.headline or "").strip() or (self.summary or "").strip()),
            bool(self.experiences),
            any(
                len(entry.raw_notes.strip()) >= _COMPLETENESS_NOTE_MIN_CHARS
                for entry in self.experiences
            ),
            bool(self.education),
            bool(self.skills),
        )
        return round(sum(1 for passed in checks if passed) / len(checks), 4)

    def with_preserved_entry_ids(self) -> CareerProfile:
        """Keep ids the client sent back; mint only for new or duplicate rows.

        Regenerating ids on save would orphan ``Bullet.profile_entry_id``.
        """
        return self.model_copy(
            update={
                "experiences": _keep_stable_entry_ids(self.experiences),
                "projects": _keep_stable_entry_ids(self.projects),
                "education": _keep_stable_entry_ids(self.education),
                "certifications": _keep_stable_entry_ids(self.certifications),
            }
        )


def _keep_stable_entry_ids(incoming: list[_EntryT]) -> list[_EntryT]:
    """Keep client-supplied ids; mint only when missing or duplicated in this list.

    Existing rows stay stable because the client sends their original ``entry_id``
    back on PUT. A second row that reuses the same id gets a fresh ObjectId so
    two experiences cannot share a bullet parent.
    """
    seen: set[str] = set()
    out: list[_EntryT] = []
    for item in incoming:
        entry_id = getattr(item, "entry_id", "")
        if not isinstance(entry_id, str) or not entry_id or entry_id in seen:
            item = item.model_copy(update={"entry_id": _new_entry_id()})
            entry_id = getattr(item, "entry_id")
        seen.add(entry_id)
        out.append(item)
    return out
