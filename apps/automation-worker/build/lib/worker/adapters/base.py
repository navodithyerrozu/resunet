"""The board adapter contract.

Why per-board adapters rather than one clever universal form-filler: the major
ATS vendors render stable, known DOMs. Greenhouse has used the same field ids
for years. A hand-written adapter against a known DOM is deterministic, fast,
and fails loudly when the form changes -- whereas a heuristic filler fails
*quietly*, by putting the right value in the wrong field. On an irreversible
submission, that distinction is the whole ballgame.

`GenericAdapter` exists for the long tail and is explicitly lower-trust: it
forces human approval regardless of configuration, because a heuristic match on
an unknown form is a guess, and a guess should not be submitted unattended.

The pipeline is a sequence of named steps so that `SubmissionAttempt.failed_step`
can record exactly where a run died. "Failed at upload_resume" is actionable;
"Playwright TimeoutError" is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from playwright.async_api import Page

from worker.browser import BrowserSession
from worker.crypto import BoardLogin


@dataclass(slots=True)
class ApplicantDetails:
    """Everything an adapter may need to fill a form.

    A flat, explicit structure rather than the raw profile document: an adapter
    should not be able to reach arbitrary user data, and this makes the set of
    fields that can reach a third-party site reviewable at a glance.
    """

    full_name: str
    first_name: str
    last_name: str
    email: str
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    portfolio_url: str | None = None
    work_authorized: bool | None = None
    requires_sponsorship: bool | None = None


@dataclass(slots=True)
class FieldMapping:
    """One resolved field. Mirrors the API model of the same name.

    `value_preview` is truncated and redacted before it reaches this struct --
    it lands in a Mongo document read by support tooling.
    """

    selector: str
    label: str | None
    value_preview: str
    strategy: str
    confidence: float


@dataclass(slots=True)
class SubmissionPayload:
    """Input to one submission run."""

    application_id: str
    job_url: str
    applicant: ApplicantDetails
    resume_pdf_path: Path
    cover_letter_text: str | None = None
    answers: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class SubmissionEvidence:
    """Proof of what happened, assembled as the run proceeds.

    Populated incrementally rather than at the end, because the interesting case
    is a run that dies partway: whatever was gathered before the failure is
    exactly what is needed to answer "did it actually submit?"
    """

    final_url: str | None = None
    confirmation_text: str | None = None
    confirmation_id: str | None = None
    field_mappings: list[FieldMapping] = field(default_factory=list)


class AdapterError(RuntimeError):
    """An adapter step failed. Carries the step name for the attempt record."""

    def __init__(self, step: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.step = step
        self.retryable = retryable


class UnmappedFieldError(AdapterError):
    """A required field could not be resolved.

    Distinct from a generic failure because the outcome is different: this is
    `NEEDS_HUMAN`, not a retry. Re-running identical logic against an identical
    form produces an identical unmapped field, so retrying wastes a slot and
    delays the person who could resolve it in fifteen seconds.
    """

    def __init__(self, step: str, label: str) -> None:
        super().__init__(step, f"could not map required field: {label!r}", retryable=False)
        self.label = label


@runtime_checkable
class BoardAdapter(Protocol):
    """One job board's submission flow.

    A Protocol rather than an ABC so adapters stay plain classes with no
    inheritance ceremony, and so a partially-implemented adapter is a type error
    at the registry rather than a `NotImplementedError` mid-submission.
    """

    board: str
    requires_login: bool
    # Overrides the global setting upward only. An adapter may demand human
    # approval; it may never waive it.
    always_require_approval: bool

    def matches(self, url: str) -> bool:
        """Whether this adapter handles the given posting URL."""
        ...

    async def open_application(self, session: BrowserSession, payload: SubmissionPayload) -> None:
        """Navigate to the posting and reach the application form."""
        ...

    async def login(self, session: BrowserSession, login: BoardLogin) -> None:
        """Authenticate. Only called when `requires_login` is True."""
        ...

    async def fill_fields(
        self, session: BrowserSession, payload: SubmissionPayload
    ) -> list[FieldMapping]:
        """Fill the identity and contact fields."""
        ...

    async def upload_resume(self, session: BrowserSession, pdf_path: Path) -> None:
        """Attach the rendered PDF."""
        ...

    async def answer_questions(
        self, session: BrowserSession, answers: dict[str, str]
    ) -> list[FieldMapping]:
        """Answer board-specific screening questions."""
        ...

    async def submit(self, session: BrowserSession) -> None:
        """Click the final submit control. The irreversible step."""
        ...

    async def verify_submitted(self, page: Page) -> SubmissionEvidence:
        """Confirm the submission landed and gather evidence."""
        ...


# Named steps, in execution order. Shared with the task so that logging,
# `failed_step`, and the approval checkpoint all refer to the same vocabulary.
STEPS: tuple[str, ...] = (
    "open_application",
    "login",
    "fill_fields",
    "upload_resume",
    "answer_questions",
    "await_approval",
    "submit",
    "verify_submitted",
)
