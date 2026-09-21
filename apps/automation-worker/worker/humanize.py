"""Human-plausible interaction timing.

Static fingerprints are only half of bot detection; the other half is
behavioural. `page.fill()` sets an input's value in a single event with no
keydown sequence and no inter-keystroke delay, which is unlike anything a person
produces. Several commercial defences score exactly this.

Two honest caveats:

* This is mitigation, not evasion. Randomised delays defeat naive timing checks.
  A defence collecting mouse trajectories and keystroke dynamics is not fooled
  by uniform jitter, and no amount of tuning here changes that.
* Every delay is real latency. The values below add roughly 15-40 seconds to a
  submission. That is a deliberate trade: a submission that takes a minute and
  lands beats one that takes five seconds and gets the account flagged.

Randomness is `random`, not `secrets`. The goal is to look organic, not to be
unpredictable to an adversary who has already won if they are reading our RNG.
"""

from __future__ import annotations

import asyncio
import random

import structlog
from playwright.async_api import Locator, Page

logger = structlog.get_logger(__name__)


async def pause(low: float = 0.4, high: float = 1.2) -> None:
    """Sleep a random interval. The unit of hesitation between actions."""
    await asyncio.sleep(random.uniform(low, high))


async def read_pause(text_length: int) -> None:
    """Pause proportionally to how long a person would take to read something.

    Submitting a form 200ms after the page paints is not a thing humans do, and
    it is one of the cheapest behavioural checks a site can run.
    """
    seconds = min(4.0, 0.6 + text_length / 900)
    await asyncio.sleep(random.uniform(seconds * 0.7, seconds * 1.3))


async def type_like_human(locator: Locator, value: str, *, per_char_ms: int = 55) -> None:
    """Type into a field with per-keystroke delays and occasional hesitation.

    Uses `press_sequentially` rather than `fill` so real key events fire. The
    extra pause after punctuation mimics the natural rhythm of composing a
    sentence, which matters for free-text fields like a cover letter box where
    a uniform cadence over 800 characters is conspicuous.
    """
    await locator.click()
    await pause(0.15, 0.45)

    # Chunked so long values do not become uniformly-spaced keystroke streams.
    for chunk in _chunks(value):
        await locator.press_sequentially(
            chunk, delay=random.uniform(per_char_ms * 0.6, per_char_ms * 1.6)
        )
        if chunk.rstrip().endswith((".", ",", ";", ":")):
            await pause(0.2, 0.7)


async def type_secret(locator: Locator, value: str) -> None:
    """Enter a credential.

    Uses `fill` deliberately -- the opposite of the advice above. Per-keystroke
    typing of a password produces a keydown event per character, and any page
    script (including a compromised or third-party one) observing the field sees
    the value assembled character by character. A single atomic set is the
    smaller exposure, and the login page is not where behavioural scoring is
    decisive anyway.

    Never logs the value; never accepts a locator it did not verify is a
    password field is a check worth adding at the call site.
    """
    await locator.click()
    await pause(0.2, 0.5)
    await locator.fill(value)
    await pause(0.3, 0.8)


async def scroll_to(page: Page, locator: Locator) -> None:
    """Scroll an element into view the way a mouse wheel would.

    Playwright auto-scrolls on click, but it jumps instantly to the target.
    Filling a ten-field form with zero scroll events between interactions is a
    strong signal, since the events are what a real viewport produces.
    """
    try:
        await locator.scroll_into_view_if_needed(timeout=4000)
        await page.mouse.wheel(0, random.randint(-70, 70))
        await pause(0.2, 0.6)
    except Exception:  # noqa: BLE001 - cosmetic; never fail a submission over it
        pass


async def move_mouse_naturally(page: Page, locator: Locator) -> None:
    """Move the pointer toward an element in a few steps rather than teleporting.

    A click with no preceding mousemove is the single most obvious behavioural
    tell. Genuine curved trajectories with acceleration would be better; this
    is the cheap version that removes the blatant case.
    """
    try:
        box = await locator.bounding_box()
        if box is None:
            return
        target_x = box["x"] + box["width"] / 2 + random.uniform(-6, 6)
        target_y = box["y"] + box["height"] / 2 + random.uniform(-4, 4)
        await page.mouse.move(target_x, target_y, steps=random.randint(6, 14))
        await pause(0.08, 0.25)
    except Exception:  # noqa: BLE001
        pass


async def click_naturally(page: Page, locator: Locator) -> None:
    """Scroll to, approach, and click an element."""
    await scroll_to(page, locator)
    await move_mouse_naturally(page, locator)
    await locator.click()
    await pause(0.3, 0.9)


def _chunks(value: str, *, size: int = 24) -> list[str]:
    """Split a string into typing bursts at word boundaries where possible."""
    if len(value) <= size:
        return [value]
    out: list[str] = []
    remaining = value
    while remaining:
        if len(remaining) <= size:
            out.append(remaining)
            break
        cut = remaining.rfind(" ", 0, size + 1)
        if cut <= 0:
            cut = size
        out.append(remaining[: cut + 1])
        remaining = remaining[cut + 1 :]
    return out
