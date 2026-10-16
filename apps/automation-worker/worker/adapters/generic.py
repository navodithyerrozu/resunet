"""Heuristic adapter for the long tail of job boards.

**This adapter always requires human approval, regardless of configuration.**
That is not conservatism for its own sake. A heuristic match on an unknown form
is a guess, and the specific failure mode is not "it didn't work" -- it is
putting a phone number in the salary field and submitting. Unattended guessing
on an irreversible action is not a tradeoff worth making, so
`always_require_approval` is True and nothing overrides it.

Field resolution is layered by decreasing reliability, and each strategy records
its own confidence so the approval UI can sort the shakiest matches to the top
of the review list. Anything below `_MIN_CONFIDENCE` is treated as unmapped and
escalated rather than filled with a low-confidence guess.
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Locator, Page

from worker.adapters.base import (
    AdapterError,
    FieldMapping,
    SubmissionEvidence,
    SubmissionPayload,
    UnmappedFieldError,
)
from worker.browser import BrowserSession
from worker.crypto import BoardLogin

logger = structlog.get_logger(__name__)

# Below this, a match is a guess rather than an inference.
_MIN_CONFIDENCE = 0.55

# Synonyms per logical field, ordered most- to least-specific. "name" is
# deliberately absent from the first-name list: it matches "Company name",
# "Referrer name" and "Legal name of guardian", and a wrong confident match is
# worse than no match.
_LABEL_SYNONYMS: dict[str, tuple[str, ...]] = {
    "first_name": ("first name", "given name", "forename"),
    "last_name": ("last name", "surname", "family name"),
    "full_name": ("full name", "your name", "applicant name"),
    "email": ("email", "e-mail", "email address"),
    "phone": ("phone", "telephone", "mobile", "cell"),
    "location": ("location", "city", "where are you based", "current location"),
    "linkedin_url": ("linkedin",),
    "github_url": ("github",),
    "portfolio_url": ("portfolio", "website", "personal site"),
}

_CONFIRMATION_TEXT = re.compile(
    r"(thank you for (applying|your application)"
    r"|application (has been )?(received|submitted|complete)"
    r"|we('ve| have) received your application"
    r"|successfully submitted)",
    re.IGNORECASE,
)


class GenericAdapter:
    board = "generic"
    requires_login = False
    always_require_approval = True  # See the module docstring. Do not relax.

    def matches(self, url: str) -> bool:
        """Fallback for everything. The registry only reaches it last."""
        return True

    async def open_application(self, session: BrowserSession, payload: SubmissionPayload) -> None:
        response = await session.page.goto(payload.job_url, wait_until="domcontentloaded")
        if response is not None and response.status >= 400:
            raise AdapterError(
                "open_application",
                f"posting returned HTTP {response.status}",
                retryable=response.status >= 500,
            )

        apply_button = session.page.locator(
            "a:has-text('Apply'), button:has-text('Apply'), "
            "a:has-text('Apply now'), button:has-text('Apply now')"
        ).first
        try:
            if await apply_button.is_visible(timeout=3000):
                await apply_button.click()
                await session.page.wait_for_load_state("domcontentloaded", timeout=20_000)
        except PlaywrightError as exc:
            logger.debug("generic.apply_navigation_probe_failed", error_type=type(exc).__name__)

        if not await _has_form(session.page):
            raise AdapterError(
                "open_application",
                "no application form found on the page -- the posting may require a "
                "login, use an external ATS, or have closed",
                retryable=False,
            )

    async def login(self, session: BrowserSession, login: BoardLogin) -> None:
        raise AdapterError(
            "login",
            "the generic adapter does not implement login flows; a board requiring "
            "authentication needs a dedicated adapter",
            retryable=False,
        )

    async def fill_fields(
        self, session: BrowserSession, payload: SubmissionPayload
    ) -> list[FieldMapping]:
        applicant = payload.applicant
        wanted: dict[str, str | None] = {
            "first_name": applicant.first_name,
            "last_name": applicant.last_name,
            "email": applicant.email,
            "phone": applicant.phone,
            "location": applicant.location,
            "linkedin_url": applicant.linkedin_url,
            "github_url": applicant.github_url,
            "portfolio_url": applicant.portfolio_url,
        }

        mappings: list[FieldMapping] = []
        for logical, value in wanted.items():
            if not value:
                continue
            resolved = await _resolve_field(session.page, logical)
            if resolved is None:
                # Only the identity fields are mandatory. A missing LinkedIn box
                # is normal; a missing email box means the form was misidentified.
                if logical in {"first_name", "last_name", "email"}:
                    if logical != "email" and await _resolve_field(
                        session.page, "full_name"
                    ) is not None:
                        continue  # single combined name field, handled below
                    raise UnmappedFieldError("fill_fields", logical)
                continue
            locator, selector, confidence = resolved
            await locator.fill(value)
            mappings.append(
                FieldMapping(
                    selector=selector,
                    label=logical,
                    value_preview=_preview(value),
                    strategy="label_heuristic",
                    confidence=confidence,
                )
            )

        combined = await _resolve_field(session.page, "full_name")
        if combined is not None and not any(m.label == "first_name" for m in mappings):
            locator, selector, confidence = combined
            await locator.fill(applicant.full_name)
            mappings.append(
                FieldMapping(
                    selector=selector,
                    label="full_name",
                    value_preview=_preview(applicant.full_name),
                    strategy="label_heuristic",
                    confidence=confidence,
                )
            )

        if payload.cover_letter_text:
            box = session.page.locator("textarea").first
            try:
                if await box.is_visible(timeout=2000):
                    await box.fill(payload.cover_letter_text)
                    mappings.append(
                        FieldMapping(
                            selector="textarea",
                            label="cover_letter",
                            value_preview=_preview(payload.cover_letter_text),
                            strategy="first_textarea",
                            # Low on purpose: the first textarea on an unknown form
                            # is as likely to be "How did you hear about us?"
                            confidence=0.5,
                        )
                    )
            except PlaywrightError as exc:
                logger.debug("generic.cover_letter_probe_failed", error_type=type(exc).__name__)

        return mappings

    async def upload_resume(self, session: BrowserSession, pdf_path: Path) -> None:
        file_input = session.page.locator("input[type='file']").first
        try:
            if not await file_input.count():
                raise AdapterError(
                    "upload_resume", "no file input found on the form", retryable=False
                )
            await file_input.set_input_files(str(pdf_path), timeout=20_000)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError("upload_resume", f"resume upload failed: {exc}") from exc

    async def answer_questions(
        self, session: BrowserSession, answers: dict[str, str]
    ) -> list[FieldMapping]:
        return await fill_by_label(session.page, answers, step="answer_questions")

    async def submit(self, session: BrowserSession) -> None:
        button = session.page.locator(
            "button[type='submit'], input[type='submit'], "
            "button:has-text('Submit'), button:has-text('Send application')"
        ).first
        if not await button.is_visible(timeout=5000):
            raise AdapterError("submit", "submit control not found", retryable=False)
        await button.click()
        try:
            await session.page.wait_for_load_state("networkidle", timeout=30_000)
        except PlaywrightError as exc:
            logger.warning(
                "generic.submit_idle_wait_failed",
                error_type=type(exc).__name__,
            )

    async def verify_submitted(self, page: Page) -> SubmissionEvidence:
        evidence = SubmissionEvidence(final_url=page.url)
        try:
            body = (await page.inner_text("body", timeout=5000))[:5000]
        except Exception:  # noqa: BLE001
            return evidence
        match = _CONFIRMATION_TEXT.search(body)
        if match:
            start = max(0, match.start() - 100)
            evidence.confirmation_text = body[start : match.end() + 200].strip()
        return evidence


# --- Shared field resolution -------------------------------------------------


async def fill_by_label(
    root: Page | Frame, answers: dict[str, str], *, step: str
) -> list[FieldMapping]:
    """Fill freeform questions by matching their visible label text.

    Shared with the vendor adapters: screening questions are custom per posting
    even on boards whose identity fields are fixed, so this heuristic is the only
    option there too. Unmatched answers are logged and skipped rather than
    guessed at -- a required screening question left blank fails visibly at
    submit, which is recoverable, whereas an answer in the wrong box is not.
    """
    mappings: list[FieldMapping] = []
    for question, answer in answers.items():
        locator = await _locator_for_label_text(root, question)
        if locator is None:
            logger.warning("generic.question_unmatched", question=question[:80])
            continue
        await locator.fill(answer)
        mappings.append(
            FieldMapping(
                selector=f"label~={question[:40]!r}",
                label=question[:120],
                value_preview=_preview(answer),
                strategy="label_text",
                confidence=0.75,
            )
        )
    return mappings


async def _resolve_field(
    root: Page | Frame, logical: str
) -> tuple[Locator, str, float] | None:
    """Find the input for a logical field, most reliable strategy first."""
    # 1. autocomplete attribute -- a spec-defined, unambiguous declaration.
    autocomplete = {
        "first_name": "given-name",
        "last_name": "family-name",
        "full_name": "name",
        "email": "email",
        "phone": "tel",
    }.get(logical)
    if autocomplete:
        selector = f"input[autocomplete='{autocomplete}']"
        locator = root.locator(selector).first
        if await _usable(locator):
            return locator, selector, 0.95

    # 2. name/id containing the logical key.
    key = logical.replace("_url", "").replace("_", "")
    for attr in ("name", "id"):
        selector = f"input[{attr}*='{key}' i]"
        locator = root.locator(selector).first
        if await _usable(locator):
            return locator, selector, 0.85

    # 3. associated <label> text.
    for synonym in _LABEL_SYNONYMS.get(logical, ()):
        labeled_locator = await _locator_for_label_text(root, synonym)
        if labeled_locator is not None:
            return labeled_locator, f"label~={synonym!r}", 0.7

    # 4. placeholder text -- weakest, since placeholders are often examples
    #    rather than descriptions ("e.g. Jane").
    for synonym in _LABEL_SYNONYMS.get(logical, ()):
        selector = f"input[placeholder*='{synonym}' i]"
        locator = root.locator(selector).first
        if await _usable(locator):
            return locator, selector, 0.6

    return None


async def _locator_for_label_text(root: Page | Frame, text: str) -> Locator | None:
    """Resolve an input via its label.

    `get_by_label` handles `<label for>`, wrapping labels, and aria-label in one
    call, which is three separate DOM patterns that would otherwise each need
    their own branch.
    """
    try:
        locator = root.get_by_label(re.compile(re.escape(text), re.IGNORECASE)).first
        if await _usable(locator):
            return locator
    except PlaywrightError as exc:
        logger.debug("generic.label_locator_probe_failed", error_type=type(exc).__name__)
    return None


async def _usable(locator: Locator) -> bool:
    """Visible, enabled, and editable.

    All three checks matter: a hidden honeypot input is a bot trap that must not
    be filled, and a readonly field silently discards what is typed into it.
    """
    try:
        if not await locator.is_visible(timeout=1200):
            return False
        if not await locator.is_enabled(timeout=800):
            return False
        return await locator.is_editable(timeout=800)
    except Exception:  # noqa: BLE001
        return False


async def _has_form(page: Page) -> bool:
    try:
        return bool(await page.locator("form, input[type='file'], input[type='email']").count())
    except Exception:  # noqa: BLE001
        return False


def _preview(value: str) -> str:
    return value[:117] + "..." if len(value) > 120 else value
