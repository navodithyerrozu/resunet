"""Deterministic parsing and profile matching without external services."""

from __future__ import annotations

from datetime import date

from bson import ObjectId

from app.api.v1.routes.jobs import _job_parse, _match_job
from app.models.job import Job, JobBoard, JobSource, JobStatus
from app.models.profile import (
    CareerProfile,
    ContactBlock,
    DateRange,
    ExperienceEntry,
    Skill,
)


def test_job_parser_extracts_only_terms_present_in_the_posting() -> None:
    parsed = _job_parse(
        "Required: Python and PostgreSQL.\nPreferred: Kubernetes.\n"
        "Build reliable payment services.",
        "Senior Backend Engineer",
    )

    assert parsed.seniority.value == "senior"
    assert [item.term for item in parsed.keywords] == ["Python", "PostgreSQL", "Kubernetes"]
    assert [item.term for item in parsed.keywords if item.required] == ["Python", "PostgreSQL"]
    assert parsed.responsibilities == ["Build reliable payment services."]


def test_match_is_based_on_profile_evidence_and_discloses_unscored_dimensions() -> None:
    profile = CareerProfile(
        user_id=str(ObjectId()),
        contact=ContactBlock(full_name="Test Candidate", email="candidate@example.com"),
        skills=[Skill(name="Python")],
        experiences=[
            ExperienceEntry(
                company="Example Co",
                title="Engineer",
                dates=DateRange(start=date(2018, 1, 1), end=date(2024, 1, 1)),
                tech_stack=["Python"],
            )
        ],
    )
    job = Job(
        id=str(ObjectId()),
        source=JobSource(board=JobBoard.MANUAL),
        company_name="Hiring Company",
        title="Senior Backend Engineer",
        description_raw="Required: Python and Kubernetes.",
        parsed=_job_parse("Required: Python and Kubernetes.", "Senior Backend Engineer"),
        status=JobStatus.OPEN,
    )

    match = _match_job(job, profile)

    assert match.matched_keywords == ["Python"]
    assert match.missing_keywords == ["Kubernetes"]
    assert match.components.semantic_similarity is None
    assert "not computed" in (match.rationale or "")
    assert 0 <= match.score <= 100
