"""Tests for the deterministic XYZ validator.

This function is the product's core guarantee -- if it passes a bullet with no
metric, a metric-free bullet reaches a PDF. So it is tested directly rather
than only through the graph, and the negative cases matter more than the
positive ones.
"""

from __future__ import annotations

import pytest

from app.models.bullet import BulletDraft, XYZViolationCode

# A well-formed bullet used as the baseline for single-defect mutations.
GOOD = {
    "action": "Rebuilt the payment retry pipeline",
    "metric": "cutting failed-charge rate from 4.2% to 0.8% across 1.2M monthly charges",
    "method": "using idempotency keys and exponential backoff in a Redis-backed queue",
}


def draft(**overrides: str) -> BulletDraft:
    return BulletDraft(**{**GOOD, **overrides})  # type: ignore[arg-type]


class TestCompliantBullets:
    def test_baseline_is_compliant(self) -> None:
        assert draft().validate_xyz() == []
        assert draft().is_compliant

    @pytest.mark.parametrize(
        "metric",
        [
            "reducing p99 latency from 800ms to 120ms",
            "for 40,000 daily active users",
            "saving roughly $250k annually in compute spend",
            "cutting deploy time from 45 minutes to under 3",
            "halving the on-call page volume for the team",  # gerund comparative
            "improving conversion by 12 percent quarter over quarter",
        ],
    )
    def test_accepts_varied_real_metrics(self, metric: str) -> None:
        """Numbers, currency, durations, and magnitude words all qualify."""
        assert draft(metric=metric).validate_xyz() == []

    @pytest.mark.parametrize(
        "metric",
        [
            "halving mean build duration",
            "halved payload size on mobile",
            "doubling trial-to-paid conversion",
            "doubled throughput on the ingest path",
            "tripling the number of indexed listings",
            "tripled nightly reconciliation coverage",
            "quadrupling regional cache hit rate",
            "quadrupled the audit sampling rate",
            "cutting release lead time in half",
        ],
    )
    def test_accepts_comparatives_in_every_inflection(self, metric: str) -> None:
        """Regression: only past-tense comparatives were matched originally.

        "halved" passed and "halving" did not, so a legitimately quantified
        bullet was rejected as having no metric at all -- the worst failure this
        validator has, because it blocks honest work rather than sloppy work.
        Both tenses of all four multipliers are pinned here so the pattern
        cannot regress to one inflection again.
        """
        assert draft(metric=metric).validate_xyz() == []


class TestMissingMetric:
    @pytest.mark.parametrize(
        "metric",
        [
            "improving overall system reliability",
            "making the checkout flow much faster",
            "significantly reducing customer complaints",
            "leading to better performance",
        ],
    )
    def test_rejects_unquantified_outcomes(self, metric: str) -> None:
        """Vague intensifiers must not pass as measurement.

        These are the exact phrasings an LLM produces when it has no number
        available, which is why they are enumerated explicitly.
        """
        codes = {v.code for v in draft(metric=metric).validate_xyz()}
        assert XYZViolationCode.MISSING_METRIC in codes

    @pytest.mark.parametrize(
        "metric",
        [
            "double-entry bookkeeping compliance",
            "double opt-in email compliance",
            "triple-redundant storage layout",
            "halves of the reporting period",
        ],
    )
    def test_bare_multiplier_words_are_not_metrics(self, metric: str) -> None:
        """`double` and `halves` as adjectives or nouns claim no measurement.

        Only the `-ed` / `-ing` inflections assert a delta. These cases exist so
        that anyone widening the pattern to the bare stem -- an easy-looking
        follow-up to the inflection fix -- has to see what it would let through
        first.
        """
        codes = {v.code for v in draft(metric=metric).validate_xyz()}
        assert XYZViolationCode.MISSING_METRIC in codes

    def test_violation_carries_a_repair_hint(self) -> None:
        """The hint is fed to the repair prompt, so it must be populated."""
        violations = draft(metric="improving things").validate_xyz()
        missing = next(v for v in violations if v.code is XYZViolationCode.MISSING_METRIC)
        assert missing.hint
        assert len(missing.hint) > 20

    def test_terse_metric_flagged_as_vague(self) -> None:
        """A bare number with no context is technically a metric but useless."""
        codes = {v.code for v in draft(metric="by 40%").validate_xyz()}
        assert XYZViolationCode.VAGUE_METRIC in codes


class TestWeakVerbs:
    @pytest.mark.parametrize(
        "action",
        [
            "Responsible for the payment retry pipeline",
            "Worked on the payment retry pipeline",
            "Helped rebuild the payment retry pipeline",
            "Assisted with the payment retry pipeline",
            "Participated in rebuilding the retry pipeline",
        ],
    )
    def test_rejects_duty_language(self, action: str) -> None:
        codes = {v.code for v in draft(action=action).validate_xyz()}
        assert XYZViolationCode.WEAK_VERB in codes

    @pytest.mark.parametrize(
        "action",
        ["Built the retry pipeline", "Shipped the retry pipeline",
         "Migrated the retry pipeline", "Automated the retry pipeline"],
    )
    def test_accepts_outcome_verbs(self, action: str) -> None:
        codes = {v.code for v in draft(action=action).validate_xyz()}
        assert XYZViolationCode.WEAK_VERB not in codes


class TestFirstPerson:
    @pytest.mark.parametrize(
        "action",
        ["I rebuilt the payment retry pipeline", "We rebuilt the payment retry pipeline"],
    )
    def test_rejects_pronouns(self, action: str) -> None:
        codes = {v.code for v in draft(action=action).validate_xyz()}
        assert XYZViolationCode.FIRST_PERSON in codes

    def test_does_not_false_positive_inside_words(self) -> None:
        """Word-boundary matching: 'Migrated' contains 'i', 'we' appears in 'weekly'."""
        d = draft(
            action="Migrated the weekly settlement job",
            method="using Airflow and a partitioned Postgres schema",
        )
        codes = {v.code for v in d.validate_xyz()}
        assert XYZViolationCode.FIRST_PERSON not in codes


class TestMethodAndLength:
    def test_rejects_missing_method(self) -> None:
        codes = {v.code for v in draft(method="somehow").validate_xyz()}
        assert XYZViolationCode.MISSING_METHOD in codes

    def test_rejects_overlong_bullet(self) -> None:
        codes = {v.code for v in draft(method="using " + "a very long toolchain " * 15).validate_xyz()}
        assert XYZViolationCode.TOO_LONG in codes

    def test_length_limit_is_configurable(self) -> None:
        """Templates differ in line width, so the ceiling is a parameter."""
        d = draft()
        assert d.validate_xyz(max_chars=500) == []
        assert any(
            v.code is XYZViolationCode.TOO_LONG for v in d.validate_xyz(max_chars=50)
        )


class TestTextAssembly:
    def test_text_is_derived_from_components(self) -> None:
        """The rendered string must come from X, Y, Z -- never stored separately."""
        d = draft()
        assert d.text.startswith(GOOD["action"])
        assert GOOD["metric"] in d.text
        assert GOOD["method"] in d.text
        assert d.text.endswith(".")

    def test_component_punctuation_is_normalised(self) -> None:
        """Trailing punctuation on components must not produce '..' when assembled."""
        d = draft(action="Built the pipeline;", method="using Redis and Celery.")
        assert ".." not in d.text
        assert d.text.count(".") == 1

    def test_multiple_violations_all_reported(self) -> None:
        """The repair prompt needs the full list, not just the first failure."""
        d = draft(
            action="I was responsible for the pipeline",
            metric="making it better",
            method="somehow",
        )
        codes = {v.code for v in d.validate_xyz()}
        assert {
            XYZViolationCode.WEAK_VERB,
            XYZViolationCode.FIRST_PERSON,
            XYZViolationCode.MISSING_METRIC,
            XYZViolationCode.MISSING_METHOD,
        } <= codes
