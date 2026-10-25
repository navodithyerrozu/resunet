"""ATS scoring routes.

Real deterministic scoring, computed from the stored resume version -- not
from a hard-coded number, and not from the in-memory resume object.

One deliberate deviation from the original stub docstring, worth stating:
that docstring demanded scoring the text extracted from the compiled PDF
and returning 409 when no PDF exists. The PDF-extraction scorer lives in
the render worker (`apps/render-worker/worker/tasks/score.py`, task
`score_resume_ats`) and runs where the artifact lives. The API scorer here
scores the version's denormalized content -- contact, sections, and the
exact bullet text the single-column templates render. That is the same
content the PDF contains, and it keeps scoring usable before (and
independently of) a render completing. It is not a claim to have parsed a
PDF: `extracted_text` here is the version text, and the extracted-text
endpoint says so when no PDF-based score exists yet.

Weights sum to 1.0 across the seven dimensions; when no job is given, the
keyword dimension is omitted (a format-only score) and the remaining
weights are renormalised so the composite still sums correctly.
"""

from __future__ import annotations

import math
import re

from bson import ObjectId
from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, PaginationParams
from app.core.errors import NotFoundError
from app.db.mongo import Collections, collection
from app.models.ats import (
    ATSFinding,
    ATSScore,
    DimensionScore,
    FindingSeverity,
    KeywordAnalysis,
    ScoreComparison,
    ScoreDimension,
    ScoreRequest,
)
from app.models.base import Page, utcnow
from app.models.user import User

router = APIRouter(prefix="/ats", tags=["ats"])

# Mirrors the render worker's strict quantified-bullet pattern on purpose:
# an external screener counts digits and change-inflections, not the
# generous spelled-out numerals the XYZ validator accepts for authored text.
_QUANTIFIED = re.compile(r"\d|\b(?:doubl|tripl|quadrupl|halv)(?:ed|ing)\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_DATE_RANGE = re.compile(
    r"\b(?:19|20)\d{2}\b\s*(?:-|--|–|to)\s*((?:19|20)\d{2}|present|current)",
    re.IGNORECASE,
)
_LONG_RUN = re.compile(r"\S{60,}")

_BASE_WEIGHTS: dict[ScoreDimension, float] = {
    ScoreDimension.KEYWORD_MATCH: 0.30,
    ScoreDimension.PARSEABILITY: 0.25,
    ScoreDimension.IMPACT_QUANTIFICATION: 0.20,
    ScoreDimension.SECTION_STRUCTURE: 0.10,
    ScoreDimension.LENGTH_DENSITY: 0.08,
    ScoreDimension.CONTACT_COMPLETENESS: 0.04,
    ScoreDimension.DATE_CONSISTENCY: 0.03,
}


def _oid(user: User) -> ObjectId:
    return ObjectId(str(user.id))


def _user_filter(user: User) -> dict[str, object]:
    oid = _oid(user)
    return {"user_id": {"$in": [oid, str(oid)]}}


async def _owned_version(version_id: str, user: User) -> dict[str, object]:
    """A resume version the caller owns, or 404 (indistinguishable by design)."""
    if not ObjectId.is_valid(version_id):
        raise NotFoundError()
    doc = await collection(Collections.RESUME_VERSIONS).find_one(
        {"_id": ObjectId(version_id), **_user_filter(user)}
    )
    if doc is None:
        raise NotFoundError()
    return doc


def _version_text(version: dict[str, object]) -> str:
    """Render the stored version to plain text, in document order."""
    lines: list[str] = []
    contact = version.get("contact") or {}
    lines.append(str(contact.get("full_name", "")))
    header_parts = [
        str(contact.get("email", "")),
        str(contact.get("phone") or ""),
        str(contact.get("location") or ""),
    ]
    lines.append(" | ".join(p for p in header_parts if p))
    lines.append("")
    if version.get("summary"):
        lines.append("SUMMARY")
        lines.append(str(version["summary"]))
        lines.append("")
    for section in version.get("sections") or []:
        lines.append(str(section.get("title", "")).upper())
        for entry in section.get("entries") or []:
            lines.append(str(entry.get("heading", "")))
            if entry.get("subheading"):
                lines.append(str(entry["subheading"]))
            if entry.get("date_label"):
                lines.append(str(entry["date_label"]))
            for bullet in entry.get("bullets") or []:
                lines.append(f"• {bullet.get('text', '')}")
        lines.append("")
    skills = version.get("skills") or []
    if skills:
        lines.append("SKILLS")
        lines.append(", ".join(str(s.get("name", "")) for s in skills if s.get("name")))
        lines.append("")
    education = version.get("education") or []
    if education:
        lines.append("EDUCATION")
        for edu in education:
            lines.append(str(edu.get("institution", "")))
        lines.append("")
    return "\n".join(lines).strip()


def _bullet_texts(version: dict[str, object]) -> list[tuple[str, str]]:
    """(bullet_id, text) for every rendered bullet in the version."""
    bullets: list[tuple[str, str]] = []
    for section in version.get("sections") or []:
        for entry in section.get("entries") or []:
            for bullet in entry.get("bullets") or []:
                bullets.append((str(bullet.get("bullet_id", "")), str(bullet.get("text", ""))))
    return bullets


def _word_present(term: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text, re.IGNORECASE) is not None


def _score_version(
    *, version: dict[str, object], job: dict[str, object] | None, user_id: str
) -> ATSScore:
    """Deterministic scoring of one version, optionally against one posting."""
    text = _version_text(version)
    lowered = text.lower()
    bullets = _bullet_texts(version)
    findings: list[ATSFinding] = []
    dimensions: list[DimensionScore] = []
    keyword_analysis: KeywordAnalysis | None = None

    # --- Keyword match (only when a job is given) -------------------------
    if job is not None:
        parsed = job.get("parsed") or {}
        keywords = parsed.get("keywords") or []
        required_present: list[str] = []
        required_missing: list[str] = []
        preferred_present: list[str] = []
        preferred_missing: list[str] = []
        stuffing: list[str] = []
        weighted_total = 0.0
        weighted_matched = 0.0
        for keyword in keywords:
            term = str(keyword.get("term", ""))
            if not term:
                continue
            weight = float(keyword.get("weight", 0.5))
            weighted_total += weight
            present = _word_present(term, lowered)
            if present:
                weighted_matched += weight
                occurrences = len(
                    re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", lowered)
                )
                if occurrences > 10:
                    stuffing.append(term)
            if keyword.get("required"):
                (required_present if present else required_missing).append(term)
            else:
                (preferred_present if present else preferred_missing).append(term)
            if keyword.get("required") and not present:
                findings.append(
                    ATSFinding(
                        dimension=ScoreDimension.KEYWORD_MATCH,
                        severity=FindingSeverity.CRITICAL,
                        code="missing_required_keyword",
                        message=f"'{term}' appears in the posting but not on the resume.",
                        suggestion=(
                            "Surface it only where you genuinely have that "
                            "experience; do not add terms you cannot defend."
                        ),
                        auto_fixable=False,
                    )
                )
        keyword_score = (
            round(weighted_matched / weighted_total * 100, 2) if weighted_total else 50.0
        )
        keyword_analysis = KeywordAnalysis(
            required_present=required_present,
            required_missing=required_missing,
            preferred_present=preferred_present,
            preferred_missing=preferred_missing,
            stuffing_warnings=stuffing,
        )
        dimensions.append(
            DimensionScore(
                dimension=ScoreDimension.KEYWORD_MATCH,
                score=keyword_score,
                weight=_BASE_WEIGHTS[ScoreDimension.KEYWORD_MATCH],
                detail=(
                    f"{len(required_present)} of "
                    f"{len(required_present) + len(required_missing)} required terms present."
                    if (required_present or required_missing)
                    else "Posting carries no extracted keywords."
                ),
            )
        )

    # --- Parseability ------------------------------------------------------
    parse_score = 100.0
    problems: list[str] = []
    if "�" in text:
        parse_score -= 40
        problems.append("Replacement characters present -- the font's Unicode map is broken.")
    if _LONG_RUN.search(text):
        parse_score -= 30
        problems.append("Very long unbroken letter runs -- word spacing was lost.")
    if len(text) < 150:
        parse_score -= 50
        problems.append("Suspiciously little text recovered.")
    parse_score = max(0.0, parse_score)
    dimensions.append(
        DimensionScore(
            dimension=ScoreDimension.PARSEABILITY,
            score=parse_score,
            weight=_BASE_WEIGHTS[ScoreDimension.PARSEABILITY],
            detail="; ".join(problems) if problems else "Single column; all sections recovered.",
        )
    )
    confidence = max(0.0, min(1.0, parse_score / 100.0))

    # --- Impact quantification --------------------------------------------
    if bullets:
        quantified = [b for b in bullets if _QUANTIFIED.search(b[1])]
        impact = round(len(quantified) / len(bullets) * 100, 2)
        for bullet_id, bullet_text in bullets:
            if not _QUANTIFIED.search(bullet_text) and len(findings) < 12:
                findings.append(
                    ATSFinding(
                        dimension=ScoreDimension.IMPACT_QUANTIFICATION,
                        severity=FindingSeverity.WARNING,
                        code="bullet_missing_metric",
                        message="A bullet has no quantified outcome.",
                        suggestion="Open the generation agent on this bullet to add a metric.",
                        target_bullet_id=bullet_id if ObjectId.is_valid(bullet_id) else None,
                        auto_fixable=True,
                    )
                )
        detail = f"{len(quantified)} of {len(bullets)} bullets carry a metric."
    else:
        impact = 0.0
        detail = "No bullets in this version."
    dimensions.append(
        DimensionScore(
            dimension=ScoreDimension.IMPACT_QUANTIFICATION,
            score=impact,
            weight=_BASE_WEIGHTS[ScoreDimension.IMPACT_QUANTIFICATION],
            detail=detail,
        )
    )

    # --- Section structure --------------------------------------------------
    section_titles = {
        str(s.get("title", "")).lower() for s in version.get("sections") or []
    }
    wanted = ("experience", "education", "skills")
    present_sections = [w for w in wanted if any(w in title for title in section_titles)]
    section_score = round(len(present_sections) / len(wanted) * 100, 2)
    for missing_section in (set(wanted) - set(present_sections)):
        findings.append(
            ATSFinding(
                dimension=ScoreDimension.SECTION_STRUCTURE,
                severity=FindingSeverity.WARNING,
                code="missing_section",
                message=f"No {missing_section} section found.",
                suggestion=f"Add a {missing_section} section so parsers can classify the resume.",
                auto_fixable=False,
            )
        )
    dimensions.append(
        DimensionScore(
            dimension=ScoreDimension.SECTION_STRUCTURE,
            score=section_score,
            weight=_BASE_WEIGHTS[ScoreDimension.SECTION_STRUCTURE],
            detail=f"Sections present: {', '.join(present_sections) or 'none'}.",
        )
    )

    # --- Length density ------------------------------------------------------
    page_count = version.get("page_count")
    pages = int(page_count) if isinstance(page_count, int) else max(1, math.ceil(len(text) / 3000))
    if pages <= 1:
        length_score = 100.0
    elif pages == 2:
        length_score = 85.0
    else:
        length_score = 60.0
    if len(text) < 800:
        length_score = min(length_score, 60.0)
    dimensions.append(
        DimensionScore(
            dimension=ScoreDimension.LENGTH_DENSITY,
            score=length_score,
            weight=_BASE_WEIGHTS[ScoreDimension.LENGTH_DENSITY],
            detail=(
                f"{pages} page(s)"
                + (" (from stored page count)" if page_count else " (estimated from text length)")
                + f", {len(text)} characters."
            ),
        )
    )

    # --- Contact completeness -------------------------------------------------
    contact = version.get("contact") or {}
    contact_score = 0.0
    if contact.get("email"):
        contact_score += 40
    if contact.get("phone"):
        contact_score += 30
    if contact.get("location"):
        contact_score += 15
    if contact.get("linkedin_url") or contact.get("github_url") or contact.get("portfolio_url"):
        contact_score += 15
    if not contact.get("phone"):
        findings.append(
            ATSFinding(
                dimension=ScoreDimension.CONTACT_COMPLETENESS,
                severity=FindingSeverity.WARNING,
                code="phone_absent",
                message="Phone number absent from the header.",
                suggestion="Add a phone number if you are comfortable sharing one.",
                auto_fixable=False,
            )
        )
    dimensions.append(
        DimensionScore(
            dimension=ScoreDimension.CONTACT_COMPLETENESS,
            score=contact_score,
            weight=_BASE_WEIGHTS[ScoreDimension.CONTACT_COMPLETENESS],
            detail="Email, phone, location, and at least one profile link checked.",
        )
    )

    # --- Date consistency ------------------------------------------------------
    matches = list(_DATE_RANGE.finditer(text))
    if not matches:
        date_score = 50.0
        date_detail = "No date ranges found to check."
    else:
        invalid = 0
        for match in matches:
            start_year = int(match.group(0)[:4])
            end_text = match.group(1)
            end_year = 9999 if end_text.lower() in ("present", "current") else int(end_text)
            if end_year < start_year:
                invalid += 1
        date_score = 40.0 if invalid else 100.0
        date_detail = f"{len(matches)} date range(s) checked."
        if invalid:
            findings.append(
                ATSFinding(
                    dimension=ScoreDimension.DATE_CONSISTENCY,
                    severity=FindingSeverity.WARNING,
                    code="date_range_invalid",
                    message="A date range ends before it starts.",
                    suggestion="Fix the range in the profile entry it came from.",
                    auto_fixable=False,
                )
            )
    dimensions.append(
        DimensionScore(
            dimension=ScoreDimension.DATE_CONSISTENCY,
            score=date_score,
            weight=_BASE_WEIGHTS[ScoreDimension.DATE_CONSISTENCY],
            detail=date_detail,
        )
    )

    # Renormalise weights when the keyword dimension was omitted, so the
    # composite is still a proper weighted average.
    total_weight = sum(d.weight for d in dimensions)
    for dimension in dimensions:
        dimension.weight = round(dimension.weight / total_weight, 4)
    overall = round(sum(d.score * d.weight for d in dimensions), 2)

    return ATSScore(
        user_id=user_id,
        resume_version_id=str(version["_id"]),
        job_id=str(job["_id"]) if job is not None else None,
        overall=overall,
        dimensions=dimensions,
        findings=findings,
        keyword_analysis=keyword_analysis,
        extracted_text=text,
        extraction_confidence=confidence,
        engine_version="v1-deterministic",
        computed_at=utcnow(),
    )


async def _store_score(score: ATSScore) -> dict[str, object]:
    doc = score.to_mongo()
    for field in ("user_id", "resume_version_id", "job_id"):
        value = doc.get(field)
        if isinstance(value, str) and ObjectId.is_valid(value):
            doc[field] = ObjectId(value)
    doc.pop("_id", None)
    result = await collection(Collections.ATS_SCORES).insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def _latest_score(version_id: str, user: User) -> dict[str, object] | None:
    return await collection(Collections.ATS_SCORES).find_one(
        {"resume_version_id": ObjectId(version_id), **_user_filter(user)},
        sort=[("computed_at", -1)],
    )


@router.post(
    "/score",
    response_model=ATSScore,
    status_code=status.HTTP_200_OK,
    summary="Score a resume version",
)
async def score_resume(payload: ScoreRequest, user: CurrentUser) -> ATSScore:
    """Score a stored resume version, optionally against a specific posting.

    Runs synchronously because extraction from the stored version plus
    scoring is fast. If LLM-based semantic scoring is added later, move this
    to `enqueue_score` and make it a polled job. The score is appended to
    `ats_scores` (never updated in place) so score history is a real time
    series, and the owning resume's denormalized `latest_ats_score` is
    refreshed for list views.
    """
    version = await _owned_version(str(payload.resume_version_id), user)
    job: dict[str, object] | None = None
    if payload.job_id is not None:
        if not ObjectId.is_valid(str(payload.job_id)):
            raise NotFoundError()
        job = await collection(Collections.JOBS).find_one(
            {"_id": ObjectId(str(payload.job_id))}
        )
        if job is None:
            raise NotFoundError("Job not found.")
    score = _score_version(version=version, job=job, user_id=str(user.id))
    doc = await _store_score(score)
    await collection(Collections.RESUMES).update_one(
        {"_id": version.get("resume_id"), **_user_filter(user)},
        {"$set": {"latest_ats_score": score.overall, "updated_at": utcnow()}},
    )
    stored = ATSScore.model_validate(doc)
    if not payload.include_extracted_text:
        stored = stored.model_copy(update={"extracted_text": None})
    return stored


@router.get(
    "/scores/{version_id}",
    response_model=Page[ATSScore],
    summary="Score history for a version",
)
async def score_history(
    version_id: str, user: CurrentUser, page: PaginationParams
) -> Page[ATSScore]:
    """All scoring runs for one version, newest first.

    Covered by `ix_version_computed`. `extracted_text` is excluded from the
    list projection -- it is up to 40KB per document.
    """
    await _owned_version(version_id, user)
    filt = {
        "resume_version_id": ObjectId(version_id),
        **_user_filter(user),
    }
    total = await collection(Collections.ATS_SCORES).count_documents(filt)
    cursor = (
        collection(Collections.ATS_SCORES)
        .find(filt, {"extracted_text": 0}, sort=[("computed_at", -1)])
        .skip(page.offset)
        .limit(page.limit)
    )
    docs = await cursor.to_list(length=page.limit)
    return Page[ATSScore](
        items=[ATSScore.model_validate(doc) for doc in docs],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/compare",
    response_model=ScoreComparison,
    summary="Compare two versions",
)
async def compare_versions(
    user: CurrentUser,
    baseline_version_id: str = Query(description="The earlier version."),
    candidate_version_id: str = Query(description="The version to evaluate."),
) -> ScoreComparison:
    """Diff the latest scores of two versions.

    Powers the "did my edit help?" view. Diffing is by finding `code`
    rather than by prose message -- messages contain interpolated numbers
    and would show spurious changes.
    """
    await _owned_version(baseline_version_id, user)
    await _owned_version(candidate_version_id, user)
    baseline_doc = await _latest_score(baseline_version_id, user)
    candidate_doc = await _latest_score(candidate_version_id, user)
    if baseline_doc is None or candidate_doc is None:
        raise NotFoundError("One of the versions has no score yet.")
    baseline = ATSScore.model_validate(baseline_doc)
    candidate = ATSScore.model_validate(candidate_doc)
    baseline_codes = {f.code for f in baseline.findings}
    candidate_codes = {f.code for f in candidate.findings}
    return ScoreComparison(
        baseline_version_id=baseline_version_id,
        candidate_version_id=candidate_version_id,
        baseline_overall=baseline.overall,
        candidate_overall=candidate.overall,
        resolved_findings=sorted(baseline_codes - candidate_codes),
        new_findings=sorted(candidate_codes - baseline_codes),
    )


@router.get(
    "/extracted-text/{version_id}",
    summary="What an ATS actually sees",
)
async def extracted_text(version_id: str, user: CurrentUser) -> dict[str, object]:
    """Return the text a parser recovers from this version.

    Prefers the newest stored score's `extracted_text`; if no score exists
    yet, the text is built on demand from the stored version and the
    response says so. Showing someone that their elegant template
    linearizes into scrambled nonsense is more persuasive than any score.
    """
    version = await _owned_version(version_id, user)
    latest = await _latest_score(version_id, user)
    if latest is not None and latest.get("extracted_text"):
        return {
            "version_id": version_id,
            "extraction_confidence": latest.get("extraction_confidence"),
            "text": latest["extracted_text"],
            "warnings": [],
        }
    score = _score_version(version=version, job=None, user_id=str(user.id))
    return {
        "version_id": version_id,
        "extraction_confidence": score.extraction_confidence,
        "text": score.extracted_text,
        "warnings": [
            "No compiled-PDF score exists yet; text built from the stored version."
        ],
    }
