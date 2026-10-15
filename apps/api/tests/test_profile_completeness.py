"""Unit tests for career-profile completeness and stable entry ids."""

from __future__ import annotations

from datetime import date

from app.models.profile import (
    CareerProfile,
    ContactBlock,
    DateRange,
    EducationEntry,
    ExperienceEntry,
    Skill,
)

USER_ID = "aaaaaaaaaaaaaaaaaaaaaaaa"


def _profile(**overrides: object) -> CareerProfile:
    data: dict[str, object] = {
        "user_id": USER_ID,
        "contact": ContactBlock(full_name="Ada Lovelace", email="ada@example.com"),
    }
    data.update(overrides)
    return CareerProfile.model_validate(data)


class TestComputeCompleteness:
    def test_registration_shape_is_one_seventh(self) -> None:
        """Name + email are the only checks a brand-new profile passes."""
        assert _profile().compute_completeness() == round(1 / 7, 4)

    def test_client_supplied_completeness_is_ignored_by_the_formula(self) -> None:
        profile = _profile(completeness=0.99)
        assert profile.compute_completeness() == round(1 / 7, 4)

    def test_reachability_headline_job_notes_education_skill(self) -> None:
        profile = _profile(
            contact=ContactBlock(
                full_name="Ada Lovelace",
                email="ada@example.com",
                location="London, UK",
            ),
            headline="Backend engineer",
            experiences=[
                ExperienceEntry(
                    company="Acme",
                    title="Engineer",
                    dates=DateRange(start=date(2023, 1, 1)),
                    raw_notes="Rebuilt retry logic and cut failed charges a lot.",
                )
            ],
            education=[EducationEntry(institution="University of London")],
            skills=[Skill(name="Python")],
        )
        assert profile.compute_completeness() == 1.0

    def test_experience_without_notes_does_not_count_as_evidenced(self) -> None:
        profile = _profile(
            experiences=[
                ExperienceEntry(
                    company="Acme",
                    title="Engineer",
                    dates=DateRange(start=date(2023, 1, 1)),
                    raw_notes="short",
                )
            ]
        )
        # identity + has-experience, but not usable raw_notes
        assert profile.compute_completeness() == round(2 / 7, 4)

    def test_projects_do_not_change_the_score(self) -> None:
        from app.models.profile import ProjectEntry

        empty = _profile()
        with_project = _profile(
            projects=[ProjectEntry(name="Side project", raw_notes="built a thing")]
        )
        assert empty.compute_completeness() == with_project.compute_completeness()


class TestStableEntryIds:
    def test_put_echo_preserves_experience_and_project_ids(self) -> None:
        original = ExperienceEntry(
            entry_id="bbbbbbbbbbbbbbbbbbbbbbbb",
            company="Acme",
            title="Engineer",
            dates=DateRange(start=date(2023, 1, 1)),
        )
        echoed = _profile(experiences=[original]).with_preserved_entry_ids()
        assert echoed.experiences[0].entry_id == "bbbbbbbbbbbbbbbbbbbbbbbb"

    def test_duplicate_ids_in_the_payload_are_split(self) -> None:
        shared = "cccccccccccccccccccccccc"
        first = ExperienceEntry(
            entry_id=shared,
            company="Acme",
            title="Engineer",
            dates=DateRange(start=date(2023, 1, 1)),
        )
        second = ExperienceEntry(
            entry_id=shared,
            company="Other",
            title="Intern",
            dates=DateRange(start=date(2022, 1, 1)),
        )
        result = _profile(experiences=[first, second]).with_preserved_entry_ids()
        ids = [row.entry_id for row in result.experiences]
        assert ids[0] == shared
        assert ids[1] != shared
        assert len(set(ids)) == 2
