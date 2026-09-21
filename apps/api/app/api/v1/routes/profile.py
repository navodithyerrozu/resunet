"""Career profile routes: the master record the resume agent draws from.

STUB: mock data plus TODOs.

The `raw_notes` field on each experience entry is the highest-leverage thing
in this module. It is where the user dumps unstructured recollection ("rebuilt
the retry logic, think it cut failures a lot, used Redis"), and it is the
primary input to the generation agent. Encourage messy, generous input here --
the agent's job is to interrogate it into XYZ form, and it cannot invent a
metric the user never mentioned.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, status

from app.api.deps import CurrentUser
from app.models.profile import (
    CareerProfile,
    ContactBlock,
    DateRange,
    EducationEntry,
    ExperienceEntry,
    Skill,
    SkillProficiency,
)

router = APIRouter(prefix="/profile", tags=["profile"])

_MOCK_ID = "000000000000000000000000"


def _mock_profile(user_id: str) -> CareerProfile:
    return CareerProfile(
        id=_MOCK_ID,
        user_id=user_id,
        contact=ContactBlock(
            full_name="Ada Lovelace",
            email="ada@example.com",
            location="London, UK",
        ),
        headline="Backend engineer, distributed systems and payments",
        experiences=[
            ExperienceEntry(
                company="Acme Corp",
                title="Senior Backend Engineer",
                location="Remote",
                dates=DateRange(start=date(2023, 1, 1)),
                tech_stack=["Python", "FastAPI", "MongoDB", "Redis", "AWS"],
                raw_notes=(
                    "Rebuilt the payment retry logic - failures dropped a lot. "
                    "Used Redis for the job queue and added idempotency keys. "
                    "Also led the migration off the old monolith."
                ),
            )
        ],
        education=[
            EducationEntry(
                institution="University of London",
                degree="BSc",
                field_of_study="Computer Science",
                dates=DateRange(start=date(2016, 9, 1), end=date(2020, 6, 1)),
            )
        ],
        skills=[
            Skill(name="Python", category="Languages",
                  proficiency=SkillProficiency.EXPERT, years=7),
            Skill(name="MongoDB", category="Databases",
                  proficiency=SkillProficiency.ADVANCED, years=4),
        ],
        completeness=0.72,
    )


@router.get("", response_model=CareerProfile, summary="Get the career profile")
async def get_profile(user: CurrentUser) -> CareerProfile:
    """Fetch the caller's profile.

    TODO: `career_profiles` has a unique index on user_id, so this is a
    single-document `find_one`. The profile is created at registration, so a
    missing document is a data-integrity bug, not a normal empty state --
    log it rather than quietly returning a blank profile.
    """
    return _mock_profile(str(user.id or _MOCK_ID))


@router.put("", response_model=CareerProfile, summary="Replace the career profile")
async def replace_profile(payload: CareerProfile, user: CurrentUser) -> CareerProfile:
    """Full replace.

    TODO: preserve existing `entry_id` values for entries the client sends
    back. If entry ids are regenerated on save, every bullet's
    `profile_entry_id` is orphaned and the user silently loses their bullets --
    the single worst failure mode in this endpoint. Match on incoming
    `entry_id` and only mint new ones for genuinely new entries.

    Recompute `completeness` on write, not on read.
    """
    return payload


@router.post(
    "/experiences",
    response_model=ExperienceEntry,
    status_code=status.HTTP_201_CREATED,
    summary="Add an experience entry",
)
async def add_experience(payload: ExperienceEntry, user: CurrentUser) -> ExperienceEntry:
    """Append one experience entry.

    TODO: `$push` onto `experiences`. A targeted push rather than a full
    document replace, so two browser tabs editing different entries do not
    clobber each other.
    """
    return payload


@router.delete(
    "/experiences/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove an experience entry",
)
async def delete_experience(entry_id: str, user: CurrentUser) -> None:
    """Remove an entry and archive its bullets.

    TODO: `$pull` the entry, then set its bullets to ARCHIVED rather than
    deleting them. Those bullets may already appear in submitted resume
    versions; deleting them breaks nothing immediately but makes the version
    history unexplainable.
    """
    return None
