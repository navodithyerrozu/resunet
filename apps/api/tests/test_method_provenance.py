"""Tests for the deterministic method-provenance gate.

The mirror of the digit gate in `nodes/mine.py`: the metric gate proves a
number was in the notes; this proves the *method* was. The live case it
exists to catch is 2026-11-01, when a borrowing-system note (no API,
database, or schema mentioned) produced the bullet "using a new API and
database schema" -- invented, and accepted, because nothing checked the
method against the notes.
"""

from __future__ import annotations

import asyncio

from app.agents.resume.nodes.validate import route_after_validation, validate_bullet
from app.agents.resume.state import initial_state
from app.models.bullet import BulletDraft, XYZViolation, XYZViolationCode

# The exact 2026-11-01 live failure, as the source note and the invented method.
SAMPLE_2_NOTES = (
    "I worked on the borrowing system and helped improve the experience "
    "for students using the library app."
)
INVENTED_METHOD = "using a new API and database schema"

SAMPLE_1_NOTES = (
    "I rebuilt the library search API with MongoDB indexes and FastAPI "
    "caching, and search time dropped by 42%."
)
GROUNDED_METHOD = "with MongoDB indexes and FastAPI caching"


def _draft(**overrides: str) -> BulletDraft:
    base = {
        "action": "Improved the borrowing experience for students",
        "metric": "increasing student usage from 50 to 200 per month",
        "method": "by optimizing the borrowing system in the library app",
    }
    base.update(overrides)
    return BulletDraft(**base)


class TestMethodProvenance:
    def test_method_copied_from_notes_passes(self) -> None:
        """The Sample 1 method names tools the notes actually name."""
        draft = _draft(
            action="Rebuilt the library search API",
            metric="search time dropped by 42%",
            method=GROUNDED_METHOD,
        )
        codes = {v.code for v in draft.validate_xyz(source_text=SAMPLE_1_NOTES)}
        assert XYZViolationCode.UNPROVEN_METHOD not in codes
        assert draft.validate_xyz(source_text=SAMPLE_1_NOTES) == []

    def test_method_restated_in_own_words_passes(self) -> None:
        """A paraphrase still shares its subject nouns with the notes."""
        codes = {v.code for v in _draft().validate_xyz(source_text=SAMPLE_2_NOTES)}
        assert XYZViolationCode.UNPROVEN_METHOD not in codes

    def test_invented_method_is_flagged(self) -> None:
        """The 2026-11-01 invented method: API/database/schema are nowhere
        in the borrowing-system note, so only stopwords overlap."""
        codes = {
            v.code for v in _draft(method=INVENTED_METHOD).validate_xyz(source_text=SAMPLE_2_NOTES)
        }
        assert XYZViolationCode.UNPROVEN_METHOD in codes

    def test_method_with_no_substantive_tokens_is_flagged(self) -> None:
        """A method built only from generic words names nothing checkable."""
        codes = {
            v.code
            for v in _draft(method="using the new system").validate_xyz(source_text=SAMPLE_2_NOTES)
        }
        assert XYZViolationCode.UNPROVEN_METHOD in codes

    def test_violation_carries_a_repair_hint(self) -> None:
        """The hint is fed to the repair prompt, so it must say what to do."""
        violations = _draft(method=INVENTED_METHOD).validate_xyz(source_text=SAMPLE_2_NOTES)
        flagged = next(v for v in violations if v.code is XYZViolationCode.UNPROVEN_METHOD)
        assert flagged.hint and "notes" in flagged.hint

    def test_no_source_text_means_shape_only(self) -> None:
        """Backward compatibility: callers with no source (manual edits)
        get the old shape-only check, not a spurious provenance failure."""
        codes = {v.code for v in _draft(method=INVENTED_METHOD).validate_xyz()}
        assert XYZViolationCode.UNPROVEN_METHOD not in codes


class TestValidateBulletProvenance:
    def _state(self, method: str):
        state = initial_state(user_id="u1", thread_id="t1", raw_notes=SAMPLE_2_NOTES)
        state.update(  # type: ignore[typeddict-item]
            achievements=[
                {
                    "id": "ach_0",
                    "raw_text": SAMPLE_2_NOTES,
                    "has_metric": False,
                    "needs_clarification": True,
                }
            ],
            current_achievement_index=0,
            current_draft=_draft(method=method),
        )
        return state

    def test_validate_bullet_flags_invented_method(self) -> None:
        result = asyncio.run(validate_bullet(self._state(INVENTED_METHOD)))
        codes = {v.code for v in result["current_violations"]}  # type: ignore[union-attr]
        assert XYZViolationCode.UNPROVEN_METHOD in codes

    def test_validate_bullet_passes_grounded_method(self) -> None:
        result = asyncio.run(
            validate_bullet(self._state("by optimizing the borrowing system in the library app"))
        )
        codes = {v.code for v in result["current_violations"]}  # type: ignore[union-attr]
        assert XYZViolationCode.UNPROVEN_METHOD not in codes


class TestProvenanceRouting:
    def _state(self, **kwargs: object):
        state = initial_state(user_id="u1", thread_id="t1", raw_notes="notes")
        state.update(kwargs)  # type: ignore[typeddict-item]
        return state

    def test_unproven_method_goes_to_repair_not_accept(self) -> None:
        """Repair, not clarify: clarify asks the user for a *number*, the
        wrong question for a method; repair can rewrite the method from the
        notes the model already has. Never silently accepted either way."""
        state = self._state(
            current_violations=[
                XYZViolation(code=XYZViolationCode.UNPROVEN_METHOD, message="unproven")
            ],
            repair_attempts=0,
        )
        assert route_after_validation(state) == "repair"

    def test_unproven_method_flags_when_budget_spent(self) -> None:
        state = self._state(
            current_violations=[
                XYZViolation(code=XYZViolationCode.UNPROVEN_METHOD, message="unproven")
            ],
            repair_attempts=99,
        )
        assert route_after_validation(state) == "flag"
