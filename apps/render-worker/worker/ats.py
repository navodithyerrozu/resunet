"""ATS scoring against text extracted from the rendered PDF.

The extraction is deliberately naive, and that is the design. pypdf's plain
`extract_text()` approximates what a mid-tier commercial resume parser does: no
OCR, no layout reconstruction, no ML. Using something smarter would produce
*better* text than the systems we are modelling, and the score would then be
optimistic in exactly the cases users most need warning about.

So the failure modes are the feature. If a ligature garbles a word here, it
garbles it at the employer too, and the user gets told.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)

# A resume that extracts to less than this either failed to parse or is an
# image. Either way the score is meaningless and the finding is "unparseable".
_MIN_VIABLE_CHARS = 200

# Every non-ASCII character in the patterns below is written as an escape rather
# than as a literal. This module's job is handling characters that are invisible
# or ambiguous on screen -- NBSP, en dash, bullet, U+FFFD, NUL -- and pasting them
# in literally makes the source unreviewable in a diff. It also broke the build
# once: a literal NUL byte in a pattern is a hard SyntaxError, and the traceback
# blames the file rather than the character.
_WHITESPACE = re.compile(r"[ \t\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")

# Mojibake signatures. Their presence means the ToUnicode map is wrong, which is
# the single most damaging PDF defect: the document looks perfect and extracts
# as noise.
_REPLACEMENT_CHARS = re.compile(r"[\ufffd\x00]")
_SUSPICIOUS_RUNS = re.compile(r"[A-Za-z]{40,}")  # words never run this long

# Section headings a parser looks for, in the forms it recognises.
_SECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "experience": re.compile(r"^\s*(work\s+)?experience|employment", re.IGNORECASE | re.MULTILINE),
    "education": re.compile(r"^\s*education", re.IGNORECASE | re.MULTILINE),
    "skills": re.compile(r"^\s*(technical\s+)?skills|competencies", re.IGNORECASE | re.MULTILINE),
}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_DATE_RANGE = re.compile(
    r"\b(19|20)\d{2}\b\s*(?:-|--|\u2013|to)\s*((19|20)\d{2}|present|current)",
    re.IGNORECASE,
)
_BULLET_LINE = re.compile(r"^\s*[\u2022\-\*]\s+", re.MULTILINE)
# What an external screener plausibly counts as a quantified bullet. Kept
# stricter than `apps/api/app/models/bullet.py::_METRIC_PATTERN` on purpose:
# that one also accepts spelled-out numerals ("a dozen", "half"), because it is
# gating a field the author explicitly labelled as a metric. This one is
# modelling somebody else's keyword scanner, so counting things a real screener
# would miss inflates the score and tells the user their resume is stronger than
# it will test in the wild. The two patterns should stay divergent on numerals
# and agree on inflections -- excluding the gerund while accepting the past
# tense of the same word was just a bug.
_QUANTIFIED = re.compile(r"\d|\b(?:doubl|tripl|quadrupl|halv)(?:ed|ing)\b", re.IGNORECASE)


@dataclass(slots=True)
class ExtractionResult:
    """What a parser recovered, plus how much to trust it."""

    text: str
    page_count: int
    confidence: float
    problems: list[str] = field(default_factory=list)

    @property
    def is_viable(self) -> bool:
        return len(self.text) >= _MIN_VIABLE_CHARS and self.confidence >= 0.3


def extract_text(pdf_bytes: bytes) -> ExtractionResult:
    """Recover text from a PDF the way an ATS would.

    Never raises on a malformed PDF: an unreadable document is a *result* here
    (confidence 0, with the reason recorded), not an exception. That is the
    finding the user needs, so it must flow through the normal path.
    """
    from pypdf import PdfReader

    problems: list[str] = []
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.warning("ats.extraction_failed", error_type=type(exc).__name__)
        return ExtractionResult(
            text="",
            page_count=0,
            confidence=0.0,
            problems=["PDF could not be parsed. Render the resume again and retry."],
        )

    raw = "\n".join(pages)
    text = _BLANK_LINES.sub("\n\n", _WHITESPACE.sub(" ", raw)).strip()

    confidence = 1.0
    if not text:
        problems.append("No text layer found. The PDF is likely an image.")
        confidence = 0.0
    else:
        if _REPLACEMENT_CHARS.search(text):
            problems.append("Replacement characters present -- the font's Unicode map is broken.")
            confidence -= 0.4
        if _SUSPICIOUS_RUNS.search(text):
            problems.append("Very long unbroken letter runs -- word spacing was lost.")
            confidence -= 0.3
        # A resume with real content averages well over 150 characters a page.
        if len(text) < 150 * max(len(pages), 1):
            problems.append("Suspiciously little text recovered relative to page count.")
            confidence -= 0.2

    return ExtractionResult(
        text=text,
        page_count=len(pages),
        confidence=max(0.0, round(confidence, 2)),
        problems=problems,
    )


# --- Dimension scoring ------------------------------------------------------
#
# Weights sum to 1.0. Parseability is weighted highest because it gates
# everything else: perfect keywords in a document that does not parse score
# zero in reality, so the composite must reflect that.

DIMENSION_WEIGHTS: dict[str, float] = {
    "parseability": 0.25,
    "keyword_match": 0.25,
    "impact_quantification": 0.20,
    "section_structure": 0.12,
    "contact_completeness": 0.08,
    "length_density": 0.06,
    "date_consistency": 0.04,
}


def score_parseability(extraction: ExtractionResult) -> tuple[float, str]:
    if not extraction.text:
        return 0.0, "No extractable text. An ATS would reject this outright."
    score = extraction.confidence * 100
    detail = "; ".join(extraction.problems) if extraction.problems else "Clean text extraction."
    return round(score, 1), detail


def score_section_structure(text: str) -> tuple[float, str]:
    found = [name for name, pattern in _SECTION_PATTERNS.items() if pattern.search(text)]
    missing = [name for name in _SECTION_PATTERNS if name not in found]
    score = 100.0 * len(found) / len(_SECTION_PATTERNS)
    detail = (
        "All standard sections detected."
        if not missing
        else f"Parser could not identify: {', '.join(missing)}."
    )
    return round(score, 1), detail


def score_impact_quantification(text: str) -> tuple[float, str]:
    """Share of bullets containing a number.

    This is the XYZ formula measured from the *output* rather than trusted from
    the input -- a useful independent check on the agent. A gap between the two
    means bullets were edited after generation, or the validator has a hole.
    """
    bullets = _BULLET_LINE.findall(text)
    if not bullets:
        return 50.0, "No bullet list structure detected; impact could not be assessed."

    lines = [ln for ln in text.split("\n") if _BULLET_LINE.match(ln)]
    quantified = sum(1 for ln in lines if _QUANTIFIED.search(ln))
    ratio = quantified / len(lines)
    return round(ratio * 100, 1), f"{quantified} of {len(lines)} bullets contain a metric."


def score_contact_completeness(text: str) -> tuple[float, str]:
    head = text[:600]  # contact details belong at the top; a parser looks there
    has_email = bool(_EMAIL.search(head))
    has_phone = bool(_PHONE.search(head))
    score = 100.0 if (has_email and has_phone) else (60.0 if has_email else 0.0)
    missing = [n for n, ok in (("email", has_email), ("phone", has_phone)) if not ok]
    detail = (
        "Contact details found."
        if not missing
        else f"Missing near the top: {', '.join(missing)}."
    )
    return score, detail


def score_length_density(text: str, page_count: int) -> tuple[float, str]:
    """Words per page, against the conventional 400-700 band.

    Too sparse reads as thin experience; too dense is unreadable and usually
    means a two-page resume was crushed onto one.
    """
    words = len(text.split())
    if page_count == 0:
        return 0.0, "No pages."
    per_page = words / page_count
    if 400 <= per_page <= 700:
        return 100.0, f"{per_page:.0f} words per page is well within range."
    if per_page < 400:
        return (
            round(max(0.0, per_page / 400 * 100), 1),
            f"Sparse at {per_page:.0f} words per page.",
        )
    return (
        round(max(0.0, 100 - (per_page - 700) / 10), 1),
        f"Dense at {per_page:.0f} words per page.",
    )


def score_date_consistency(text: str) -> tuple[float, str]:
    ranges = _DATE_RANGE.findall(text)
    if not ranges:
        return 40.0, "No parseable date ranges found; employment dates may be misread."
    return 100.0, f"{len(ranges)} date ranges parsed cleanly."


def score_keyword_match(
    text: str, *, required: list[str], preferred: list[str]
) -> tuple[float, dict[str, list[str]]]:
    """Coverage of the target posting's keywords.

    Matching is case-insensitive and word-boundary anchored: substring matching
    would count "Java" inside "JavaScript", which is the classic false positive
    in every naive keyword scorer.
    """
    lowered = text.lower()

    def present(term: str) -> bool:
        return bool(re.search(rf"\b{re.escape(term.lower())}\b", lowered))

    req_present = [t for t in required if present(t)]
    req_missing = [t for t in required if t not in req_present]
    pref_present = [t for t in preferred if present(t)]
    pref_missing = [t for t in preferred if t not in pref_present]

    # Required terms dominate; preferred ones are a smaller bonus.
    req_ratio = len(req_present) / len(required) if required else 1.0
    pref_ratio = len(pref_present) / len(preferred) if preferred else 1.0
    score = (req_ratio * 0.8 + pref_ratio * 0.2) * 100

    # Stuffing check: the same term more than five times reads as gaming, and
    # both modern ATS ranking and human reviewers penalise it.
    stuffed = [
        t for t in required + preferred
        if len(re.findall(rf"\b{re.escape(t.lower())}\b", lowered)) > 5
    ]

    return round(score, 1), {
        "required_present": req_present,
        "required_missing": req_missing,
        "preferred_present": pref_present,
        "preferred_missing": pref_missing,
        "stuffing_warnings": stuffed,
    }
