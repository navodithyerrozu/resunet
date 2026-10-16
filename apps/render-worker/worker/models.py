"""Consumer-side read models for the render worker.

These mirror a *subset* of `apps/api/app/models/resume.py`. The worker does not
import the API package on purpose: doing so would pull FastAPI, python-jose,
passlib and LangGraph into a 700MB texlive image in order to obtain one Pydantic
class, and would couple the render image's build to the API's dependency tree.

The tradeoff is real -- these definitions can drift from the API's. Two things
keep that honest:

* Only fields the templates actually consume are declared, so the coupling
  surface is as small as it can be. Adding a field to the API model requires no
  change here.
* `extra="ignore"` means the worker tolerates new fields but *fails loudly* on a
  renamed or removed one, at parse time, with the field name in the error --
  rather than rendering a resume with a silently blank section.

If a third consumer of these documents appears, extract `packages/py-shared`
and delete this file.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class ReadModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ContactRead(ReadModel):
    full_name: str
    email: str
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None


class BulletRead(ReadModel):
    text: str
    skills: list[str] = Field(default_factory=list)


class EntryRead(ReadModel):
    entry_id: str
    heading: str
    subheading: str | None = None
    date_label: str
    location: str | None = None
    bullets: list[BulletRead] = Field(default_factory=list)


class SectionRead(ReadModel):
    kind: str
    title: str
    order: int
    entries: list[EntryRead] = Field(default_factory=list)


class DateRangeRead(ReadModel):
    start: date
    end: date | None = None

    def label(self) -> str:
        start = self.start.strftime("%b %Y")
        end = self.end.strftime("%b %Y") if self.end else "Present"
        return f"{start} – {end}"


class SkillRead(ReadModel):
    name: str
    category: str | None = None


class EducationRead(ReadModel):
    institution: str
    degree: str | None = None
    field_of_study: str | None = None
    dates: DateRangeRead | None = None
    gpa: float | None = None
    location: str | None = None

    @property
    def date_label(self) -> str | None:
        return self.dates.label() if self.dates else None


class CertificationRead(ReadModel):
    name: str
    issuer: str | None = None
    issued_on: date | None = None
    expires_on: date | None = None

    @property
    def date_label(self) -> str | None:
        if self.issued_on is None:
            return None
        if self.expires_on is None:
            return self.issued_on.strftime("%b %Y")
        return (
            f"{self.issued_on.strftime('%b %Y')} – "
            f"{self.expires_on.strftime('%b %Y')}"
        )


class ResumeVersionRead(ReadModel):
    """A frozen resume version, as the renderer needs it.

    Everything here is already denormalized by the API before the render is
    enqueued, which is what makes the worker's job pure: no joins, no
    ownership checks, no chance of picking up a bullet edited mid-compile.
    """

    id: str = Field(alias="_id")
    resume_id: str
    user_id: str
    version: int
    template: str

    contact: ContactRead
    summary: str | None = None
    sections: list[SectionRead] = Field(default_factory=list)
    skills: list[SkillRead] = Field(default_factory=list)
    education: list[EducationRead] = Field(default_factory=list)
    certifications: list[CertificationRead] = Field(default_factory=list)

    rendered_at: datetime | None = None

    def ordered_sections(self) -> list[SectionRead]:
        """Sections in render order, with empty ones dropped.

        A heading with nothing under it is a visible defect in a PDF and, for
        an ATS, a section keyword with no supporting content.
        """
        populated = [s for s in self.sections if s.entries or s.kind in {"summary", "skills"}]
        return sorted(populated, key=lambda s: s.order)
