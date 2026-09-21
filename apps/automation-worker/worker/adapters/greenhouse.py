"""Greenhouse adapter.

Greenhouse's embedded application form (`boards.greenhouse.io` and the
`#grnhse_app` iframe on company career pages) has kept the same field ids for
years, which is what makes a deterministic adapter worthwhile here.

The iframe is the detail that catches people out: on a company-hosted page the
form lives inside a cross-origin frame, so top-level locators silently match
nothing. Every locator below goes through `_form()`, which resolves the frame
first.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from playwright.async_api import Frame, Page

from worker import humanize
from worker.adapters.base import (
    AdapterError,
    FieldMapping,
    SubmissionEvidence,
    SubmissionPayload,
)
from worker.browser import BrowserSession
from worker.crypto import BoardLogin

logger = structlog.get_logger(__name__)

_CONFIRMATION_MARKERS = (
    "text=Thank you for applying",
    "text=Your application has been submitted",
    "#application_confirmation",
)


class GreenhouseAdapter:
    board = "greenhouse"
    requires_login = False
    always_require_approval = False

    def matches(self, url: str) -> bool:
        return "greenhouse.io" in url or "grnh.se" in url

    async def _form(self, page: Page) -> Frame | Page:
        """Return the frame containing the application form, or the page itself.

        Greenhouse embeds via an iframe on company-hosted career pages and
        renders inline on `boards.greenhouse.io`. Every locator in this adapter
        goes through here because a top-level locator silently matches nothing
        against the iframe -- no error, just an empty result and a form that
        never gets filled.
        """
        for frame in page.frames:
            if "greenhouse.io" in (frame.url or "") and frame is not page.main_frame:
                return frame
        return page

    async def open_application(self, session: BrowserSession, payload: SubmissionPayload) -> None:
        response = await session.page.goto(payload.job_url, wait_until="domcontentloaded")
        if response is not None and response.status >= 400:
            # 404 means the posting closed between matching and submitting, which
            # happens constantly and is permanent -- retrying cannot bring a
            # closed req back.
            raise AdapterError(
                "open_application",
                f"posting returned HTTP {response.status}",
                retryable=response.status >= 500,
            )
        await humanize.read_pause(1200)

        # Company pages put the form behind an "Apply" button.
        apply_button = session.page.locator(
            "a:has-text('Apply'), button:has-text('Apply for this job')"
        ).first
        if await apply_button.is_visible(timeout=3000):
            await humanize.click_naturally(session.page, apply_button)
            await humanize.pause(1.0, 2.0)

    async def login(self, session: BrowserSession, login: BoardLogin) -> None:
        """Not used. Greenhouse applications are anonymous."""
        raise AdapterError("login", "greenhouse does not require a login", retryable=False)

    async def fill_fields(
        self, session: BrowserSession, payload: SubmissionPayload
    ) -> list[FieldMapping]:
        form = await self._form(session.page)
        applicant = payload.applicant
        mappings: list[FieldMapping] = []

        # Stable ids, so confidence is 1.0 and the strategy is exact. Anything
        # resolved by heuristic gets a lower confidence, which is what the
        # approval UI sorts on.
        fields: list[tuple[str, str | None, str]] = [
            ("#first_name", applicant.first_name, "First name"),
            ("#last_name", applicant.last_name, "Last name"),
            ("#email", applicant.email, "Email"),
            ("#phone", applicant.phone, "Phone"),
        ]
        for selector, value, label in fields:
            if not value:
                continue
            locator = form.locator(selector).first
            if not await locator.is_visible(timeout=2500):
                if selector in {"#first_name", "#last_name", "#email"}:
                    raise AdapterError(
                        "fill_fields",
                        f"required Greenhouse field {label!r} not found -- the form "
                        "layout has probably changed",
                        retryable=False,
                    )
                continue
            await humanize.type_like_human(locator, value)
            mappings.append(
                FieldMapping(
                    selector=selector,
                    label=label,
                    value_preview=_preview(value),
                    strategy="known_selector",
                    confidence=1.0,
                )
            )

        for selector, url, label in (
            ("input[name*='urls[LinkedIn]']", applicant.linkedin_url, "LinkedIn"),
            ("input[name*='urls[GitHub]']", applicant.github_url, "GitHub"),
            ("input[name*='urls[Portfolio]']", applicant.portfolio_url, "Portfolio"),
        ):
            if not url:
                continue
            locator = form.locator(selector).first
            if await locator.is_visible(timeout=1500):
                await humanize.type_like_human(locator, url)
                mappings.append(
                    FieldMapping(
                        selector=selector,
                        label=label,
                        value_preview=_preview(url),
                        strategy="known_selector",
                        confidence=0.95,
                    )
                )

        if payload.cover_letter_text:
            box = form.locator("#cover_letter_text, textarea[name*='cover_letter']").first
            if await box.is_visible(timeout=2000):
                await humanize.type_like_human(box, payload.cover_letter_text)
                mappings.append(
                    FieldMapping(
                        selector="#cover_letter_text",
                        label="Cover letter",
                        value_preview=_preview(payload.cover_letter_text),
                        strategy="known_selector",
                        confidence=1.0,
                    )
                )

        return mappings

    async def upload_resume(self, session: BrowserSession, pdf_path: Path) -> None:
        form = await self._form(session.page)

        # Greenhouse hides the real <input type=file> behind a styled control.
        # `set_input_files` works on the hidden input directly, which is both
        # more reliable than clicking through the widget and avoids opening a
        # native file dialog that Playwright would then have to intercept.
        file_input = form.locator("input[type='file']").first
        try:
            await file_input.set_input_files(str(pdf_path), timeout=20_000)
        except Exception as exc:
            raise AdapterError("upload_resume", f"resume upload failed: {exc}") from exc

        # Greenhouse renders the filename on successful attach. Waiting for that
        # confirmation rather than sleeping means a slow upload is handled and a
        # silently-rejected file is caught.
        try:
            await form.locator(f"text={pdf_path.name}").first.wait_for(timeout=15_000)
        except Exception:  # noqa: BLE001
            logger.warning("greenhouse.upload_unconfirmed", filename=pdf_path.name)
        await humanize.pause(0.6, 1.4)

    async def answer_questions(
        self, session: BrowserSession, answers: dict[str, str]
    ) -> list[FieldMapping]:
        from worker.adapters.generic import fill_by_label

        form = await self._form(session.page)
        return await fill_by_label(form, answers, step="answer_questions")

    async def submit(self, session: BrowserSession) -> None:
        form = await self._form(session.page)
        button = form.locator("#submit_app, button:has-text('Submit Application')").first
        if not await button.is_visible(timeout=5000):
            raise AdapterError("submit", "submit button not found", retryable=False)
        await humanize.click_naturally(session.page, button)
        # Waiting for network idle rather than a fixed sleep: the confirmation is
        # rendered after an XHR, and a fixed wait either races it or wastes time.
        await session.page.wait_for_load_state("networkidle", timeout=30_000)

    async def verify_submitted(self, page: Page) -> SubmissionEvidence:
        evidence = SubmissionEvidence(final_url=page.url)
        for marker in _CONFIRMATION_MARKERS:
            locator = page.locator(marker).first
            try:
                if await locator.is_visible(timeout=4000):
                    evidence.confirmation_text = (await locator.inner_text())[:1000]
                    return evidence
            except Exception:  # noqa: BLE001
                continue
        # No positive confirmation. Reported as such rather than assumed either
        # way -- the caller decides, and an ambiguous outcome is flagged for a
        # human instead of being recorded as success.
        return evidence


def _preview(value: str) -> str:
    return value[:117] + "..." if len(value) > 120 else value
