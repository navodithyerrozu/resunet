"""Isolated, unmodified Playwright browser contexts for authorized workflows.

Browser fingerprints are left untouched. Every network request is restricted
to the worker's exact hostname allowlist; blocks, CAPTCHA, and MFA remain
terminal conditions. A fresh context per application prevents cookies from
crossing user boundaries. Tracing begins only after credential entry to avoid
recording passwords.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import structlog
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    ProxySettings,
    Route,
    async_playwright,
)

from worker.settings import AutomationSettings, get_settings

logger = structlog.get_logger(__name__)

@dataclass(slots=True)
class BrowserSession:
    """One browser context plus its page and evidence paths."""

    context: BrowserContext
    page: Page
    trace_path: Path | None
    # Tracing starts later than the context does (see `start_trace`), so teardown
    # cannot infer from `trace_path` alone whether there is anything to stop.
    # Without this, every pre-login failure calls `tracing.stop()` on a context
    # that never started tracing and logs a warning for it -- which trains you to
    # ignore the warning that means something.
    tracing_started: bool = False


@asynccontextmanager
async def playwright_runtime() -> AsyncIterator[Playwright]:
    """Own the Playwright driver for the worker's lifetime.

    Started once per process, not per job: the driver spawns a Node
    subprocess, and paying that cost per submission would dominate the runtime
    of short jobs.
    """
    async with async_playwright() as p:
        yield p


async def launch_browser(playwright: Playwright) -> Browser:
    """Launch an unmodified Playwright browser for supported workflows."""
    settings = get_settings()
    proxy: ProxySettings | None = (
        {"server": settings.automation_proxy_url} if settings.automation_proxy_url else None
    )

    return await playwright.chromium.launch(
        headless=settings.automation_headless,
        channel=settings.automation_browser_channel,
        proxy=proxy,
        # Chromium's own default is 30s; nav timeouts are set per-context below.
        timeout=60_000,
    )


@asynccontextmanager
async def browser_session(
    browser: Browser,
    *,
    application_id: str,
    capture_trace: bool | None = None,
) -> AsyncIterator[BrowserSession]:
    """Yield an isolated Playwright context and guarantee its teardown.

    A context per submission, never a shared one. Two reasons: cookies from one
    user's session must not reach another's submission, and a context that has
    been challenged carries that state -- reusing it spreads a block instead of
    containing it.

    The `finally` block is not optional. A leaked BrowserContext holds 100MB+
    and its renderer processes stay resident; a handful of leaks on a
    long-running worker ends in the OOM killer taking the process mid-submission,
    which is the worst possible time.
    """
    settings = get_settings()
    trace_enabled = (
        settings.automation_capture_trace if capture_trace is None else capture_trace
    )

    context = await browser.new_context(
        java_script_enabled=True,
        permissions=[],
        ignore_https_errors=False,
    )
    context.set_default_navigation_timeout(settings.automation_nav_timeout_ms)
    context.set_default_timeout(settings.automation_action_timeout_ms)

    trace_path: Path | None = None
    if trace_enabled:
        # SECURITY: tracing with snapshots records the DOM, including the values
        # of input elements. A trace captured across a login step therefore
        # contains the user's job-board password in plaintext, inside a zip that
        # support tooling later opens. Tracing is consequently started *after*
        # authentication (`worker/tasks/submit.py` calls `start_trace` post-login)
        # and `snapshots` stays on only because form-field evidence is the whole
        # point of the trace for the non-credential steps.
        trace_path = settings.trace_storage_dir / f"{application_id}.zip"

    page = await context.new_page()

    async def restrict_navigation(route: Route) -> None:
        await _restrict_navigation(route, settings, application_id)

    await context.route("**/*", restrict_navigation)
    session = BrowserSession(context=context, page=page, trace_path=trace_path)
    try:
        yield session
    finally:
        # Ordering matters: stop tracing before closing the context or the zip
        # is truncated and the evidence is lost exactly when it was needed.
        if session.tracing_started and trace_path is not None:
            try:
                await context.tracing.stop(path=str(trace_path))
            except Exception:  # noqa: BLE001 - teardown must not mask the real error
                logger.warning("browser.trace_stop_failed", application_id=application_id)
        await context.close()


async def start_trace(session: BrowserSession) -> None:
    """Begin tracing. Called only after any credential entry has completed."""
    if session.trace_path is None or session.tracing_started:
        return
    session.trace_path.parent.mkdir(parents=True, exist_ok=True)
    await session.context.tracing.start(screenshots=True, snapshots=True, sources=False)
    session.tracing_started = True


async def capture_screenshot(page: Page, path: Path) -> Path | None:
    """Save a full-page screenshot as submission evidence.

    Never allowed to raise. This runs on the failure path, and an exception here
    would replace a useful "the form rejected the phone number" error with a
    screenshot bug.

    Note that these images contain the full contents of a submitted application
    -- name, address, work history -- so retention is bounded by
    `automation_trace_retention_days` and access should be treated as access to
    personal data.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path), full_page=True)
        return path
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("browser.screenshot_failed", error=str(exc))
        return None


async def _restrict_navigation(
    route: Route, settings: AutomationSettings, application_id: str
) -> None:
    request = route.request
    host_allowed = settings.is_application_url_authorized(request.url)
    if not host_allowed:
        logger.warning(
            "browser.request_not_authorized",
            application_id=application_id,
            host=urlsplit(request.url).hostname,
        )
        await route.abort("blockedbyclient")
        return
    await route.continue_()
