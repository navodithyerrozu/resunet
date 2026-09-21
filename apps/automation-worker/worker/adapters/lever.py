"""Lever adapter.

Lever's form is simpler than Greenhouse's -- no iframe, and it uses a single
combined `name` field rather than first/last, which is the one thing worth
handling carefully. The `/apply` suffix is required: a bare posting URL renders
the description with an Apply button, and going straight to `/apply` skips a
navigation and a click that could each fail.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from playwright.async_api import Page

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


class LeverAdapter:
    board = "lever"
    requires_login = False
    always_require_approval = False

    def matches(self, url: str) -> bool:
        return "lever.co" in url

    async def open_application(self, session: BrowserSession, payload: SubmissionPayload) -> None:
        url = payload.job_url.rstrip("/")
        if not url.endswith("/apply"):
            url = f"{url}/apply"

        response = await session.page.goto(url, wait_until="domcontentloaded")
        if response is not None and response.status >= 400:
            raise AdapterError(
                "open_application",
                f"posting returned HTTP {response.status}",
                retryable=response.status >= 500,
            )
        await humanize.read_pause(1000)

        if not await session.page.locator("input[name='name']").first.is_visible(timeout=8000):
            raise AdapterError(
                "open_application",
                "Lever application form did not render -- the posting may be closed",
                retryable=False,
            )

    async def login(self, session: BrowserSession, login: BoardLogin) -> None:
        raise AdapterError("login", "lever does not require a login", retryable=False)

    async def fill_fields(
        self, session: BrowserSession, payload: SubmissionPayload
    ) -> list[FieldMapping]:
        applicant = payload.applicant
        page = session.page
        mappings: list[FieldMapping] = []

        # Lever takes one combined name field. Sending "First Last" is correct
        # here; splitting it would be the bug.
        for selector, value, label, required in (
            ("input[name='name']", applicant.full_name, "Full name", True),
            ("input[name='email']", applicant.email, "Email", True),
            ("input[name='phone']", applicant.phone, "Phone", False),
            ("input[name='org']", None, "Current company", False),
            ("input[name='urls[LinkedIn]']", applicant.linkedin_url, "LinkedIn", False),
            ("input[name='urls[Github]']", applicant.github_url, "GitHub", False),
            ("input[name='urls[Portfolio]']", applicant.portfolio_url, "Portfolio", False),
        ):
            if not value:
                continue
            locator = page.locator(selector).first
            if not await locator.is_visible(timeout=2500):
                if required:
                    raise AdapterError(
                        "fill_fields",
                        f"required Lever field {label!r} not found",
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

        if payload.cover_letter_text:
            box = page.locator("textarea[name='comments']").first
            if await box.is_visible(timeout=2000):
                await humanize.type_like_human(box, payload.cover_letter_text)
                mappings.append(
                    FieldMapping(
                        selector="textarea[name='comments']",
                        label="Additional information",
                        value_preview=_preview(payload.cover_letter_text),
                        strategy="known_selector",
                        confidence=1.0,
                    )
                )

        return mappings

    async def upload_resume(self, session: BrowserSession, pdf_path: Path) -> None:
        file_input = session.page.locator("input[name='resume'], input[type='file']").first
        try:
            await file_input.set_input_files(str(pdf_path), timeout=20_000)
        except Exception as exc:
            raise AdapterError("upload_resume", f"resume upload failed: {exc}") from exc

        # Lever parses the resume server-side and pre-fills fields from it. That
        # parse can overwrite what was already typed, so the wait here is not
        # cosmetic -- filling before it completes loses data silently.
        await humanize.pause(2.0, 3.5)

    async def answer_questions(
        self, session: BrowserSession, answers: dict[str, str]
    ) -> list[FieldMapping]:
        from worker.adapters.generic import fill_by_label

        return await fill_by_label(session.page, answers, step="answer_questions")

    async def submit(self, session: BrowserSession) -> None:
        button = session.page.locator(
            "button[type='submit'], button:has-text('Submit application')"
        ).first
        if not await button.is_visible(timeout=5000):
            raise AdapterError("submit", "submit button not found", retryable=False)
        await humanize.click_naturally(session.page, button)
        await session.page.wait_for_load_state("networkidle", timeout=30_000)

    async def verify_submitted(self, page: Page) -> SubmissionEvidence:
        evidence = SubmissionEvidence(final_url=page.url)
        # Lever redirects to /thanks on success, which is a stronger signal than
        # any text match -- a URL change cannot be faked by a validation banner.
        if "/thanks" in page.url or "thank" in page.url.lower():
            evidence.confirmation_text = "Redirected to Lever confirmation page."
            return evidence
        try:
            banner = page.locator("text=Thank you").first
            if await banner.is_visible(timeout=4000):
                evidence.confirmation_text = (await banner.inner_text())[:1000]
        except Exception:  # noqa: BLE001
            pass
        return evidence


def _preview(value: str) -> str:
    return value[:117] + "..." if len(value) > 120 else value
