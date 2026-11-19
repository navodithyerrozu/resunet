"""The XYZ bullet — the core content primitive of ResuNet.

Design note (this is the load-bearing decision in the schema)
------------------------------------------------------------
A bullet is stored as its *components*, not as a finished sentence:

    X -- what you did          (action + object)
    Y -- measured by what      (the quantified outcome)
    Z -- using what method     (tools, techniques, systems)

The rendered one-liner is *derived* from those parts, never authored
directly. This makes XYZ compliance a property of the type system rather
than a request in a prompt: a bullet with no metric cannot be constructed,
so it cannot be stored, so it cannot reach a PDF. Prompt-level instructions
like "always include a metric" fail some percentage of the time; a required
field fails zero percent of the time.

`BulletDraft` is the LLM's structured output target. `Bullet` is the
persisted document. The agent's validate/repair loop sits between them.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import Field, computed_field, field_validator

from app.models.base import ApiModel, MongoModel, PyObjectId

# Digits, spelled-out numerals, or comparative words that imply measurement.
#
# This gate is deliberately *generous*, and it is the opposite of
# `render-worker/worker/ats.py::_QUANTIFIED`, which is deliberately strict. The
# two are not meant to agree, and the divergence is the point:
#
#   - This pattern inspects the `metric` field of a structured draft -- text the
#     author typed into a box labelled "measured by what". The field's existence
#     already proves intent, so all this needs to catch is fluff like "improved
#     things" typed into a metric box. Rejecting a real measurement here blocks
#     the user from saving honest work, which is the worse error.
#   - `_QUANTIFIED` models an *external* ATS reading extracted PDF text. There,
#     generosity is flattery: it would report a score the real screener will not
#     give. Being stricter than reality is safe; being looser is a lie.
#
# Inflections matter more than they look. Only past-tense forms were listed
# originally, so "halving mean build duration" -- a bullet with a perfectly good
# measured claim -- was rejected as having no metric at all.
#
# The bare and plural forms (`double`, `triple`, `halves`) are omitted on
# purpose, not forgotten: they are usually adjectives or nouns rather than
# assertions of change ("double-entry bookkeeping", "double opt-in", "the two
# halves of the migration"). `-ed` and `-ing` are the forms that actually claim
# a measured delta.
_METRIC_PATTERN = re.compile(
    r"""(
        \d                                   # any digit: 40%, 2.3s, 15k
        | \b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten
            |dozen|hundred|thousand|million|billion)\b
        | \bhalf\b                           # "cut build time in half"
        | \b(?:doubl|tripl|quadrupl|halv)(?:ed|ing)\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Openers that signal duty-listing rather than impact.
_WEAK_OPENERS = frozenset(
    {
        "responsible",
        "worked",
        "helped",
        "assisted",
        "participated",
        "involved",
        "tasked",
        "duties",
        "handled",
        "supported",
        "contributed",
    }
)

# Words that may precede the real verb without changing its meaning:
# a first-person pronoun ("I", "we") and the auxiliary it pairs with
# ("was", "were"). Without skipping them, "I was responsible for ..." was
# missed entirely, because the first token checked was "I" rather than
# "responsible". Only leading tokens are skipped -- a weak word buried
# mid-sentence ("Built a team responsible for ...") is an adjective there,
# not a duty-listing opener, and must not be flagged.
_WEAK_OPENER_PREFIXES = frozenset(
    {
        "i",
        "we",
        "my",
        "our",
        "me",
        "us",
        "was",
        "were",
        "am",
        "is",
        "are",
    }
)


def _has_weak_opener(action: str) -> str | None:
    """Return the weak opener verb, or None.

    The verb is the first token that is neither a leading pronoun nor its
    auxiliary, so "Responsible for ..." and "I was responsible for ..."
    are both caught, while "I rebuilt ..." (verb: "rebuilt") is not.
    """
    tokens = re.findall(r"[a-z]+", action.lower())
    for token in tokens:
        if token in _WEAK_OPENER_PREFIXES:
            continue
        return token if token in _WEAK_OPENERS else None
    return None


_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")

# Words that carry no evidence about *how* the work was done. A method built
# only from these -- "using a new API and database schema" -- names nothing
# the notes could confirm or deny, so it must not count as provenance.
# Deliberately includes the generic nouns an LLM reaches for when inventing
# a method (system, platform, schema, workflow) and the filler adjectives
# (new, streamlined, robust) that make an invented method sound concrete.
_PROVENANCE_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "by",
        "from",
        "at",
        "as",
        "into",
        "through",
        "via",
        "using",
        "used",
        "use",
        "uses",
        "i",
        "we",
        "my",
        "our",
        "me",
        "us",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "can",
        "could",
        "should",
        "not",
        "no",
        "up",
        "out",
        "over",
        "under",
        "after",
        "before",
        "between",
        "among",
        "per",
        "each",
        "new",
        "improved",
        "improve",
        "improving",
        "helped",
        "help",
        "worked",
        "work",
        "streamlined",
        "efficient",
        "better",
        "best",
        "various",
        "multiple",
        "several",
        "different",
        "complete",
        "full",
        "entire",
        "modern",
        "advanced",
        "robust",
        "scalable",
        "reliable",
        "effective",
        "powerful",
        "simple",
        "easy",
        "fast",
        "quickly",
        "seamlessly",
        "successfully",
        "directly",
        "end",
        "system",
        "systems",
        "app",
        "application",
        "applications",
        "platform",
        "platforms",
        "software",
        "tool",
        "tools",
        "technology",
        "technologies",
        "technique",
        "techniques",
        "method",
        "methods",
        "approach",
        "approaches",
        "solution",
        "solutions",
        "process",
        "processes",
        "workflow",
        "workflows",
        "pipeline",
        "pipelines",
        "framework",
        "frameworks",
        "library",
        "libraries",
        "database",
        "databases",
        "schema",
        "schemas",
        "api",
        "apis",
        "service",
        "services",
        "code",
        "codebase",
        "program",
        "programs",
        "website",
        "web",
        "data",
        "information",
        "feature",
        "features",
        "functionality",
        "component",
        "components",
        "module",
        "modules",
        "architecture",
        "infrastructure",
        "interface",
        "interfaces",
        "model",
        "models",
        "algorithm",
        "algorithms",
        "structure",
        "structures",
        "design",
        "implementation",
        "development",
        "engineering",
        "experience",
        "experiences",
        "user",
        "users",
        "team",
        "teams",
        "business",
        "project",
        "projects",
        "task",
        "tasks",
        "job",
        "company",
        "product",
        "products",
        "result",
        "results",
        "outcome",
        "outcomes",
        "performance",
        "quality",
        "value",
        "time",
        "way",
        "ways",
        "part",
        "parts",
        "thing",
        "things",
        "stuff",
        "one",
        "two",
    }
)


def _substantive_tokens(text: str) -> set[str]:
    """Lowercased tokens that could evidence a method, stopwords removed.

    Single- and two-letter tokens are dropped: they are almost always
    pronouns, articles, or version noise ("v2"), not tool names.
    """
    return {
        token
        for token in _TOKEN_PATTERN.findall(text.lower())
        if len(token) >= 3 and token not in _PROVENANCE_STOPWORDS
    }


def check_method_provenance(method: str, source_text: str) -> XYZViolation | None:
    """Deterministic check that a draft's method comes from the source notes.

    The mirror of the digit gate in `nodes/mine.py`: the metric gate proves a
    number was in the notes; this proves the *method* was. A method with no
    substantive token overlap with the notes is treated as invented -- the
    live 2026-11-01 failure ("using a new API and database schema" for a
    borrowing-system note that mentions no API, database, or schema) is the
    case this exists to catch.

    One shared substantive token is enough to pass: a real method restated in
    the model's own words ("optimizing the borrowing system") still shares
    its subject nouns with the notes, while an invented method shares
    nothing but stopwords. A paraphrase that shares no token at all is
    flagged rather than trusted -- asking the user or flagging for review
    is the safe error, exactly as with a missing metric.
    """
    method_tokens = _substantive_tokens(method)
    if not method_tokens:
        return XYZViolation(
            code=XYZViolationCode.UNPROVEN_METHOD,
            message="The Z component names no concrete tool or technique "
            "that appears in your notes.",
            hint="Rewrite the method using only tools or techniques named "
            "in the achievement notes.",
        )
    if not method_tokens & _substantive_tokens(source_text):
        return XYZViolation(
            code=XYZViolationCode.UNPROVEN_METHOD,
            message="The Z component names tools or techniques that do not appear in your notes.",
            hint="Rewrite the method using only tools or techniques named "
            "in the achievement notes.",
        )
    return None


class BulletStatus(StrEnum):
    """Lifecycle of a bullet through the generate/validate/approve pipeline."""

    DRAFT = "draft"  # produced, not yet validated
    NEEDS_REVIEW = "needs_review"  # failed validation past the repair budget
    APPROVED = "approved"  # passed validation and/or user-confirmed
    ARCHIVED = "archived"  # retired, retained for history


class BulletSource(StrEnum):
    """Provenance. Drives UI affordances and trust in scoring."""

    USER_AUTHORED = "user_authored"
    AGENT_GENERATED = "agent_generated"
    AGENT_REWRITTEN = "agent_rewritten"  # user text improved by the agent
    IMPORTED = "imported"  # parsed from an uploaded resume


class XYZViolationCode(StrEnum):
    """Machine-readable validation failures.

    These are fed back into the repair node so the retry prompt can name the
    specific defect instead of saying "try again", which measurably improves
    single-pass repair rates.
    """

    MISSING_METRIC = "missing_metric"
    VAGUE_METRIC = "vague_metric"
    MISSING_METHOD = "missing_method"
    UNPROVEN_METHOD = "unproven_method"
    WEAK_VERB = "weak_verb"
    FIRST_PERSON = "first_person"
    TOO_LONG = "too_long"
    PAST_TENSE_REQUIRED = "past_tense_required"


class XYZViolation(ApiModel):
    """A single validation failure with enough context to guide a repair."""

    code: XYZViolationCode
    message: str = Field(description="Human-readable explanation shown in the UI.")
    hint: str | None = Field(
        default=None,
        description="Concrete suggestion passed to the repair node.",
    )


class BulletDraft(ApiModel):
    """Structured-output target for the LLM.

    Bound to the model via `with_structured_output`, so the model must emit
    the three components separately. It is never asked to write a bullet
    sentence -- assembly happens in code, which removes an entire class of
    formatting drift.
    """

    action: str = Field(
        min_length=3,
        max_length=200,
        description="X: what you did. Starts with a strong past-tense verb.",
    )
    metric: str = Field(
        min_length=1,
        max_length=160,
        description="Y: the quantified result. MUST contain a number, percentage, "
        "or magnitude. 'reduced p99 latency from 800ms to 120ms', not 'improved "
        "performance'.",
    )
    method: str = Field(
        min_length=2,
        max_length=200,
        description="Z: how you did it -- named tools, systems, or techniques. "
        "'by introducing Redis-backed caching and query batching'.",
    )
    skills: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="Canonical skill tags extracted from this bullet; feeds ATS keyword matching.",
    )

    @field_validator("action", "metric", "method")
    @classmethod
    def _no_trailing_period(cls, v: str) -> str:
        """Strip terminal punctuation so assembled text has exactly one."""
        return v.strip().rstrip(".;,")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def text(self) -> str:
        """The assembled one-line bullet.

        Derived, never stored as an independent editable field -- that would
        let the rendered text drift away from its components.
        """
        return f"{self.action} {self.metric} {self.method}.".replace("  ", " ")

    def validate_xyz(
        self, *, max_chars: int = 240, source_text: str | None = None
    ) -> list[XYZViolation]:
        """Deterministic XYZ compliance check.

        Runs in code rather than via an LLM judge: it is free, instant, and
        identical across runs, which matters because it gates persistence.
        The LLM is only asked to *fix* what this function reports.

        `source_text`, when given, is the achievement notes the draft was
        built from. It enables the method-provenance check: a method whose
        substantive tokens never appear in the notes is flagged as invented,
        the same way a metric with no number is flagged as missing. Callers
        that have no source (e.g. a manual bullet edit) omit it and get the
        shape-only check, unchanged from before.
        """
        violations: list[XYZViolation] = []

        if not _METRIC_PATTERN.search(self.metric):
            violations.append(
                XYZViolation(
                    code=XYZViolationCode.MISSING_METRIC,
                    message="The Y component has no quantity.",
                    hint="Add a number, percent, duration, or scale -- e.g. "
                    "'cutting build time 12min -> 90s' or 'for 40k daily users'.",
                )
            )
        elif len(self.metric.split()) < 3:
            violations.append(
                XYZViolation(
                    code=XYZViolationCode.VAGUE_METRIC,
                    message="The Y component is too terse to convey impact.",
                    hint="State what the number measures and its baseline.",
                )
            )

        weak_opener = _has_weak_opener(self.action)
        if weak_opener is not None:
            violations.append(
                XYZViolation(
                    code=XYZViolationCode.WEAK_VERB,
                    message=f"'{weak_opener}' describes a duty, not an accomplishment.",
                    hint="Open with an outcome verb: Built, Shipped, Reduced, "
                    "Automated, Migrated, Designed.",
                )
            )

        if re.search(r"\b(i|my|me|we|our)\b", self.text, re.IGNORECASE):
            violations.append(
                XYZViolation(
                    code=XYZViolationCode.FIRST_PERSON,
                    message="Resume bullets omit first-person pronouns.",
                    hint="Drop 'I'/'we' and lead with the verb.",
                )
            )

        if len(self.method.split()) < 2:
            violations.append(
                XYZViolation(
                    code=XYZViolationCode.MISSING_METHOD,
                    message="The Z component does not name a concrete method or tool.",
                    hint="Name the technology or technique -- 'using PyTorch and "
                    "distributed data-parallel training'.",
                )
            )
        elif source_text is not None:
            # Only when a method was actually named: a missing method is
            # already reported above, and provenance of nothing is meaningless.
            provenance = check_method_provenance(self.method, source_text)
            if provenance is not None:
                violations.append(provenance)

        if len(self.text) > max_chars:
            violations.append(
                XYZViolation(
                    code=XYZViolationCode.TOO_LONG,
                    message=f"{len(self.text)} characters exceeds the {max_chars} limit "
                    "and will wrap to a third line.",
                    hint="Tighten the method clause; keep the metric intact.",
                )
            )

        return violations

    @property
    def is_compliant(self) -> bool:
        return not self.validate_xyz()


class Bullet(MongoModel):
    """Persisted bullet. Collection: `bullets`.

    Owned by a user and scoped to one experience entry on their career
    profile. Kept in its own collection rather than embedded in resumes
    because a single bullet is reused across many tailored resumes and needs
    independent scoring and reuse analytics.
    """

    user_id: PyObjectId
    profile_entry_id: PyObjectId = Field(
        description="The experience/project entry on the career profile this belongs to."
    )

    action: str
    metric: str
    method: str
    skills: list[str] = Field(default_factory=list)

    status: BulletStatus = BulletStatus.DRAFT
    source: BulletSource = BulletSource.AGENT_GENERATED
    violations: list[XYZViolation] = Field(
        default_factory=list,
        description="Outstanding failures when status is NEEDS_REVIEW.",
    )

    thread_id: str | None = Field(
        default=None,
        description="LangGraph thread that produced this bullet; enables replay.",
    )
    repair_attempts: int = Field(
        default=0,
        ge=0,
        description="Repair passes consumed. Guards against unbounded LLM loops.",
    )

    embedding: list[float] | None = Field(
        default=None,
        description="Vector for semantic job matching. Populated asynchronously; "
        "backed by an Atlas Vector Search index in production.",
    )
    times_used: int = Field(default=0, ge=0, description="Resume versions including this bullet.")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def text(self) -> str:
        return f"{self.action} {self.metric} {self.method}.".replace("  ", " ")

    def as_draft(self) -> BulletDraft:
        """Project back to the draft shape for re-validation or repair."""
        return BulletDraft(
            action=self.action,
            metric=self.metric,
            method=self.method,
            skills=self.skills,
        )


class BulletUpdate(ApiModel):
    """PATCH body for manual bullet editing. Every field optional.

    An edit re-validates the XYZ shape deterministically and resets the
    bullet to DRAFT, so an edited bullet must be re-approved (POST
    .../bullets/{id}/approve) before it can reach a render -- editing a
    metric or method must never silently keep an old APPROVED stamp.
    """

    action: str | None = Field(default=None, min_length=3, max_length=200)
    metric: str | None = Field(default=None, min_length=1, max_length=160)
    method: str | None = Field(default=None, min_length=2, max_length=200)
    skills: list[str] | None = Field(default=None, max_length=12)
