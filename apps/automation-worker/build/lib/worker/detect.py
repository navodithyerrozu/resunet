"""Detecting that a site has blocked us.

Detection has to happen *before* the worker keeps interacting, for a reason
that is easy to get wrong: a Cloudflare interstitial is a normal HTTP 200 with
a normal DOM. Selectors do not error, `fill()` succeeds against nothing, and a
naive run will report "submitted" having typed a cover letter into a challenge
page. Every classification below exists because the failure it catches
otherwise presents as success.

Classification feeds `retry.py`, which treats these outcomes as terminal. The
instinct to retry harder against a bot wall is precisely wrong: repeated
attempts escalate an IP-level rate limit into a range ban, and the egress IP is
shared by every user of the platform.
"""

from __future__ import annotations

import re
from enum import StrEnum

import structlog
from playwright.async_api import Page, Response

logger = structlog.get_logger(__name__)


class BlockReason(StrEnum):
    """Mirrors `apps/api/app/models/application.py::BlockReason`."""

    CAPTCHA = "captcha"
    CLOUDFLARE = "cloudflare"
    RATE_LIMITED = "rate_limited"
    LOGIN_REQUIRED = "login_required"
    MFA_REQUIRED = "mfa_required"
    IP_BLOCKED = "ip_blocked"
    UNKNOWN = "unknown"


# DOM selectors, checked first because they are unambiguous. Text matching is
# the fallback and is noisier -- a page *about* CAPTCHAs would match the text
# heuristic, which is why selector evidence is preferred.
_CAPTCHA_SELECTORS: tuple[str, ...] = (
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[title*='challenge']",
    "div.g-recaptcha",
    "div.h-captcha",
    "div[class*='captcha']",
    "#px-captcha",                 # PerimeterX
    "iframe[src*='arkoselabs']",   # FunCaptcha / Arkose
    "iframe[src*='turnstile']",    # Cloudflare Turnstile
)

_CLOUDFLARE_SELECTORS: tuple[str, ...] = (
    "#challenge-form",
    "#cf-challenge-running",
    "div.cf-browser-verification",
    "#cf-wrapper",
)

_MFA_SELECTORS: tuple[str, ...] = (
    "input[autocomplete='one-time-code']",
    "input[name*='otp' i]",
    "input[name*='mfa' i]",
    "input[name*='verification_code' i]",
)

_LOGIN_SELECTORS: tuple[str, ...] = (
    "input[type='password']",
    "form[action*='login']",
    "form[action*='signin']",
)

_BLOCK_TEXT = re.compile(
    r"(verify (you are|you're) (a )?human"
    r"|unusual (traffic|activity)"
    r"|are you a robot"
    r"|checking your browser"
    r"|just a moment"
    r"|access denied"
    r"|you have been blocked"
    r"|automated (access|requests)"
    r"|rate limit(ed)?"
    r"|too many requests"
    r"|suspicious activity)",
    re.IGNORECASE,
)

_RATE_LIMIT_TEXT = re.compile(r"(rate limit|too many requests|slow down)", re.IGNORECASE)


async def classify_response(response: Response | None) -> BlockReason | None:
    """Check HTTP status before touching the DOM.

    403 and 429 are the honest signals; everything else in this module exists
    because most defences do not bother sending one.
    """
    if response is None:
        return None
    status = response.status
    if status == 429:
        return BlockReason.RATE_LIMITED
    if status == 403:
        # Cloudflare's own block page uses 403 with a distinctive header.
        server = (response.headers.get("server") or "").lower()
        if "cloudflare" in server:
            return BlockReason.CLOUDFLARE
        return BlockReason.IP_BLOCKED
    if status == 503 and "cloudflare" in (response.headers.get("server") or "").lower():
        return BlockReason.CLOUDFLARE
    return None


async def detect_block(page: Page) -> BlockReason | None:
    """Inspect a loaded page for evidence that we are being challenged.

    Returns None when the page looks like a normal application form. Ordering is
    by specificity: an explicit CAPTCHA widget is a stronger signal than the
    word "blocked" appearing somewhere in the body text.
    """
    for selector in _CAPTCHA_SELECTORS:
        if await _present(page, selector):
            logger.warning("detect.captcha", selector=selector, url=page.url)
            return BlockReason.CAPTCHA

    for selector in _CLOUDFLARE_SELECTORS:
        if await _present(page, selector):
            logger.warning("detect.cloudflare", selector=selector, url=page.url)
            return BlockReason.CLOUDFLARE

    for selector in _MFA_SELECTORS:
        if await _present(page, selector):
            # Distinct from LOGIN_REQUIRED: an MFA prompt means the password was
            # accepted, so the credentials are valid and must not be marked dead.
            logger.info("detect.mfa_required", url=page.url)
            return BlockReason.MFA_REQUIRED

    text = await _visible_text(page)
    if _RATE_LIMIT_TEXT.search(text):
        return BlockReason.RATE_LIMITED
    if _BLOCK_TEXT.search(text):
        logger.warning("detect.block_text", url=page.url)
        return BlockReason.UNKNOWN

    return None


async def detect_login_wall(page: Page) -> bool:
    """Whether the page is asking for credentials.

    Checked separately from `detect_block` because a login wall is a normal,
    expected state for boards that require an account -- it routes to the
    credential flow rather than to a failure.
    """
    return any([await _present(page, selector) for selector in _LOGIN_SELECTORS])


async def _present(page: Page, selector: str) -> bool:
    """Whether a selector matches a visible element.

    Visibility is required, not just presence. Job boards routinely ship a
    hidden reCAPTCHA div that only activates on suspicion, and treating that as
    a block would fail every submission to those boards.
    """
    try:
        locator = page.locator(selector).first
        return await locator.is_visible(timeout=1200)
    except Exception:  # noqa: BLE001 - a missing selector is the common case
        return False


async def _visible_text(page: Page, *, limit: int = 5000) -> str:
    """Body text, truncated. Never raises -- detection must not itself fail."""
    try:
        content = await page.inner_text("body", timeout=3000)
    except Exception:  # noqa: BLE001
        return ""
    return content[:limit]
