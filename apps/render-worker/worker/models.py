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

from datetime import datetime

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


class SkillRead(ReadModel):
    name: str
    category: str | None = None


class EducationRead(ReadModel):
    institution: str
    degree: str
    field_of_study: str | None = None
    date_label: str | None = None
    gpa: str | None = None
    location: str | None = None


class CertificationRead(ReadModel):
    name: str
    issuer: str | None = None
    date_label: str | None = None


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

        Only experience/projects (and any future kind the template does not
        render separately) come back here. Summary, skills, education, and
        certifications are rendered by dedicated blocks in `_base.tex.j2`
        from `summary`, `skill_groups`, `education`, and `certifications`
        -- including them in this loop as well used to print each of those
        headings twice, and printed empty Summary/Skills headings even on
        a resume with no summary or skills at all.
        """
        _SEPARATELY_RENDERED = {"summary", "skills", "education", "certifications"}
        populated = [
            s
            for s in self.sections
            if s.kind not in _SEPARATELY_RENDERED and s.entries
        ]
        return sorted(populated, key=lambda s: s.order)
