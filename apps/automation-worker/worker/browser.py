"""Hardened Playwright browser factory.

Read this before changing anything in it.

**Automation detection is an arms race that the automating side loses.**
Commercial bot defences (Cloudflare Turnstile, PerimeterX, DataDome, Akamai)
fingerprint dozens of signals, run TLS-level JA3/JA4 checks the browser cannot
influence, and increasingly weight *behavioural* telemetry -- mouse paths,
keystroke cadence, scroll dynamics -- over static properties. The patches below
close the handful of trivially-queryable tells. They will not defeat a
determined defence, and pretending otherwise leads to building retry logic that
hammers a wall.

The operational consequences, which shape the rest of this worker:

1. Detection is treated as a *terminal* outcome, not a retryable one. When a
   bot wall appears the correct response is to stop and ask a human. See
   `detect.py` and `retry.py`.
2. CAPTCHAs are never solved, programmatically or via a solving service. That
   line is drawn on three grounds: it is prohibited by essentially every job
   board's terms, third-party solvers require shipping them the page contents
   including personal data, and a solved CAPTCHA on a detected session usually
   escalates to an account ban rather than access.
3. The proxy matters more than any of this code. Datacenter egress is frequently
   blocked before a single line of JavaScript is evaluated, which is why
   `settings.py` refuses to boot in production without one.

**Also worth stating plainly:** automated submission is prohibited by the terms
of service of many job boards. Users should be told which boards are supported
via an official API or partner integration versus driven through the UI, and
the latter carries a real risk of their account being suspended. That is a
product decision this module cannot make, but it should not be buried.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import structlog
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from worker.settings import get_settings

logger = structlog.get_logger(__name__)

# Removes the `navigator.webdriver` flag at the source. Doing it via a launch
# flag rather than an init script matters: the flag is set before any page
# script runs, so a defence that reads it in an inline <head> script sees the
# patched value. An init-script override is applied a fraction later and some
# detectors deliberately read it early.
_LAUNCH_ARGS: tuple[str, ...] = (
    "--disable-blink-features=AutomationControlled",
    # Cuts an entire class of divergence between our browser and a real user's.
    "--disable-features=IsolateOrigins,site-per-process,Translate",
    "--disable-infobars",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-dev-shm-usage",  # belt and braces alongside compose's shm_size
    "--window-position=0,0",
)

# Deliberately NOT included: --no-sandbox. It is the usual reflex for Chromium
# in Docker and it disables the process sandbox, meaning a browser exploit
# executes with the worker's privileges -- in a process holding the credential
# decryption key. The official Playwright image configures `pwuser` so the
# sandbox works without it.

# Residual static tells that a launch flag does not cover. Kept small and
# targeted; a sprawling stealth patch is itself a fingerprint, because no real
# browser has this exact combination of overridden properties.
_STEALTH_INIT_SCRIPT = """
// A headless Chromium reports an empty plugin array. Real Chrome has entries.
Object.defineProperty(navigator, 'plugins', {
  get: () => [
    { name: 'PDF Viewer', filename: 'internal-pdf-viewer' },
    { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer' },
  ],
});

// navigator.languages must agree with the Accept-Language header the context
// sends. A mismatch between the two is a cheap, reliable detection signal.
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

// window.chrome is absent in headless builds and present in real Chrome.
if (!window.chrome) {
  window.chrome = { runtime: {}, loadTimes: () => ({}), csi: () => ({}) };
}

// Notification permission reads 'denied' under automation and 'default' in a
// fresh real profile.
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) =>
  parameters.name === 'notifications'
    ? Promise.resolve({ state: Notification.permission || 'default' })
    : originalQuery(parameters);

// SwiftShader gives away software rendering. Report a plausible discrete GPU.
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function (parameter) {
  if (parameter === 37445) return 'Intel Inc.';
  if (parameter === 37446) return 'Intel Iris OpenGL Engine';
  return getParameter.apply(this, [parameter]);
};
"""

# Kept current-ish and consistent with the bundled Chromium's major version. A
# UA claiming Chrome 131 from a browser whose JS features match Chrome 129 is
# contradictory and trivially caught, so this must be reviewed when the
# Playwright pin moves.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)


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
    """Launch the browser process."""
    settings = get_settings()
    proxy = {"server": settings.automation_proxy_url} if settings.automation_proxy_url else None

    if proxy is None and settings.is_production:  # pragma: no cover - guarded in settings
        raise RuntimeError("refusing to launch without a proxy in production")
    if proxy is None:
        logger.warning(
            "browser.no_proxy_configured",
            detail="Submitting from the host IP. Expect blocks on any board with "
            "commercial bot defences.",
        )

    return await playwright.chromium.launch(
        headless=settings.automation_headless,
        channel=settings.automation_browser_channel,
        args=list(_LAUNCH_ARGS),
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
    """Yield a fresh, hardened context and guarantee its teardown.

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
        user_agent=_USER_AGENT,
        locale=settings.automation_locale,
        timezone_id=settings.automation_timezone,
        # Jittered by a few pixels. A fleet of contexts reporting byte-identical
        # viewports is itself a cluster signal across sessions.
        viewport={
            "width": settings.automation_viewport_width + random.randint(-8, 8),  # noqa: S311
            "height": settings.automation_viewport_height + random.randint(-8, 8),  # noqa: S311
        },
        device_scale_factor=1,
        is_mobile=False,
        has_touch=False,
        java_script_enabled=True,
        # Consistency with `navigator.languages` in the init script above.
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        # Deny rather than prompt. A permission dialog blocks the automation and
        # a granted geolocation is a fingerprinting surface we gain nothing from.
        permissions=[],
        ignore_https_errors=False,
    )
    context.set_default_navigation_timeout(settings.automation_nav_timeout_ms)
    context.set_default_timeout(settings.automation_action_timeout_ms)
    await context.add_init_script(_STEALTH_INIT_SCRIPT)

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

    session = BrowserSession(context=context, page=await context.new_page(), trace_path=trace_path)
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
