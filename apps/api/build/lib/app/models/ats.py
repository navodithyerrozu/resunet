"""ATS scoring models.

The important design decision: a score is computed against the **text
extracted from the rendered PDF**, not against the LaTeX source or the
in-memory resume object.

That is the whole point of the feature. A resume can look immaculate to a
human and still lose half its content to an ATS parser -- because a glyph
came from a ligature, or a two-column layout linearized in the wrong order,
or a text box was skipped entirely. Scoring the source would happily award a
100 to a document no parser can read. So `ATSScore.extracted_text` holds
what a parser actually saw, and every finding is derived from that.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, computed_field

from app.models.base import ApiModel, AppModel, MongoModel, PyObjectId


class ScoreDimension(StrEnum):
    """The seven axes of the composite score."""

    KEYWORD_MATCH = "keyword_match"
    PARSEABILITY = "parseability"
    IMPACT_QUANTIFICATION = "impact_quantification"
    SECTION_STRUCTURE = "section_structure"
    LENGTH_DENSITY = "length_density"
    CONTACT_COMPLETENESS = "contact_completeness"
    DATE_CONSISTENCY = "date_consistency"


class FindingSeverity(StrEnum):
    CRITICAL = "critical"  # content will be lost or misread
    WARNING = "warning"  # measurably weakens the application
    INFO = "info"  # polish


class ATSFinding(ApiModel):
    """One actionable defect.

    `auto_fixable` matters: those are surfaced as a one-click "Fix all"
    action, which is where most of the perceived value of the scorer lives.
    """

    dimension: ScoreDimension
    severity: FindingSeverity
    code: str = Field(description="Stable machine code, e.g. 'missing_required_keyword'.")
    message: str = Field(description="What is wrong, in plain language.")
    suggestion: str | None = Field(default=None, description="How to fix it.")
    target_bullet_id: PyObjectId | None = Field(
        default=None, description="Bullet this finding points at, when applicable."
    )
    auto_fixable: bool = False


class DimensionScore(AppModel):
    dimension: ScoreDimension
    score: float = Field(ge=0, le=100)
    weight: float = Field(ge=0, le=1, description="Contribution to the composite.")
    detail: str | None = None


class KeywordAnalysis(AppModel):
    """Keyword coverage against the target posting."""

    required_present: list[str] = Field(default_factory=list)
    required_missing: list[str] = Field(default_factory=list)
    preferred_present: list[str] = Field(default_factory=list)
    preferred_missing: list[str] = Field(default_factory=list)
    stuffing_warnings: list[str] = Field(
        default_factory=list,
        description="Terms repeated past the point of credibility. Modern ATS "
        "ranking penalises this, and human reviewers notice it immediately.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def required_coverage(self) -> float:
        total = len(self.required_present) + len(self.required_missing)
        return 1.0 if total == 0 else len(self.required_present) / total


class ATSScore(MongoModel):
    """Collection: `ats_scores`.

    Append-only: a new document per scoring run rather than an update in
    place, so the score history over successive resume versions is a real
    time series the user can watch improve.
    """

    user_id: PyObjectId
    resume_version_id: PyObjectId = Field(
        description="Scores attach to an immutable version, never a mutable resume, "
        "so a score can never drift out of sync with the document it describes."
    )
    job_id: PyObjectId | None = Field(
        default=None,
        description="None means a format-only score with keyword dimensions omitted.",
    )

    overall: float = Field(ge=0, le=100)
    dimensions: list[DimensionScore] = Field(default_factory=list)
    findings: list[ATSFinding] = Field(default_factory=list)
    keyword_analysis: KeywordAnalysis | None = None

    extracted_text: str | None = Field(
        default=None,
        max_length=40_000,
        description="Text recovered from the rendered PDF by the same extraction path "
        "an ATS uses. Shown to the user as 'what the robot sees' -- the single most "
        "convincing artifact in the product.",
    )
    extraction_confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Low values mean the PDF barely parsed, which is itself the finding.",
    )

    engine_version: str = Field(default="v1")
    computed_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def grade(self) -> str:
        """Letter grade. Users act on 'C+' far more readily than on '78.4'."""
        thresholds = [
            (93, "A"),
            (90, "A-"),
            (87, "B+"),
            (83, "B"),
            (80, "B-"),
            (77, "C+"),
            (73, "C"),
            (70, "C-"),
            (60, "D"),
        ]
        return next((g for cutoff, g in thresholds if self.overall >= cutoff), "F")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity is FindingSeverity.CRITICAL)


# --- API contracts ---------------------------------------------------------


class ScoreRequest(ApiModel):
    resume_version_id: PyObjectId
    job_id: PyObjectId | None = Field(default=None, description="Omit for a format-only score.")
    include_extracted_text: bool = Field(
        default=False, description="Adds ~20KB to the response; off by default."
    )


class ScoreSubmission(ApiModel):
    """Accepted background scoring request; clients read the result from history."""

    status: Literal["queued"] = "queued"
    resume_version_id: PyObjectId
    arq_job_id: str | None = None


class ScoreComparison(ApiModel):
    """Before/after delta across two versions, for the 'did my edit help?' view."""

    baseline_version_id: PyObjectId
    candidate_version_id: PyObjectId
    baseline_overall: float
    candidate_overall: float
    resolved_findings: list[str] = Field(default_factory=list)
    new_findings: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def delta(self) -> float:
        return round(self.candidate_overall - self.baseline_overall, 2)
