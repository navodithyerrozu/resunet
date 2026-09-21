"""Building the template context from a stored resume version.

Kept separate from the render task so it can be unit-tested without Mongo,
LaTeX or a queue -- given a `ResumeVersionRead`, this produces a plain dict,
and that is the whole contract.
"""

from __future__ import annotations

from typing import Any

from worker.models import ResumeVersionRead, SkillRead

# Conventional ordering for skill categories. Recruiters skim top-down and
# hiring signal is concentrated in languages and frameworks; "Tools" last is
# the standard reading order, and matching convention costs nothing.
_CATEGORY_ORDER: tuple[str, ...] = (
    "Languages",
    "Frameworks",
    "Databases",
    "Cloud",
    "Tools",
    "Practices",
)
_UNCATEGORISED = "Additional"


class SkillGroup:
    """A named group of skills, ready for a `Category: a, b, c` line."""

    __slots__ = ("items", "name")

    def __init__(self, name: str, items: list[str]) -> None:
        self.name = name
        self.items = items


def _contact_parts(version: ResumeVersionRead) -> list[str]:
    """Assemble the header line, omitting anything unset.

    Order is deliberate: location and phone first because they are what a
    recruiter scans for, links last because they are the longest strings.
    URLs are stripped of their scheme -- `https://` costs horizontal space in
    the tightest line of the document and communicates nothing.
    """
    contact = version.contact
    parts: list[str] = []
    if contact.location:
        parts.append(contact.location)
    if contact.phone:
        parts.append(contact.phone)
    parts.append(contact.email)
    for url in (contact.linkedin_url, contact.github_url, contact.portfolio_url):
        if url:
            parts.append(url.replace("https://", "").replace("http://", "").rstrip("/"))
    return parts


def _group_skills(skills: list[SkillRead]) -> list[SkillGroup]:
    """Bucket skills by category, preserving in-category input order.

    Input order is the user's stated priority, so it is not re-sorted --
    alphabetising would bury the thing they most want seen.
    """
    buckets: dict[str, list[str]] = {}
    for skill in skills:
        buckets.setdefault(skill.category or _UNCATEGORISED, []).append(skill.name)

    def sort_key(name: str) -> tuple[int, str]:
        try:
            return (_CATEGORY_ORDER.index(name), "")
        except ValueError:
            # Unknown categories sort after known ones, alphabetically.
            return (len(_CATEGORY_ORDER), name)

    return [SkillGroup(name, buckets[name]) for name in sorted(buckets, key=sort_key)]


def build_context(version: ResumeVersionRead) -> dict[str, Any]:
    """Produce the dict handed to Jinja.

    Note what is *not* here: no ids, no timestamps, no user metadata. The
    template can only render what it is given, so keeping the context minimal
    bounds what a template bug can leak into a document the user sends to a
    stranger.
    """
    return {
        "contact": version.contact,
        "contact_parts": _contact_parts(version),
        "summary": version.summary,
        "sections": version.ordered_sections(),
        "skill_groups": _group_skills(version.skills),
        "education": version.education,
        "certifications": version.certifications,
    }
