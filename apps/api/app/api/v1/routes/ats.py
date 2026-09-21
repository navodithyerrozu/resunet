"""ATS scoring routes.

STUB: returns realistic mock scores. The mock is intentionally not a perfect
100 -- it shows the two findings that matter most in practice (a missing
required keyword and a bullet without a metric) so the frontend gets built
against the case that actually occurs.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, PaginationParams
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

router = APIRouter(prefix="/ats", tags=["ats"])

_MOCK_ID = "000000000000000000000000"


def _mock_score(version_id: str, user_id: str, job_id: str | None) -> ATSScore:
    """Representative score with the failure modes we actually see."""
    return ATSScore(
        id=_MOCK_ID,
        user_id=user_id,
        resume_version_id=version_id,
        job_id=job_id,
        overall=87.5,
        dimensions=[
            DimensionScore(
                dimension=ScoreDimension.KEYWORD_MATCH, score=82.0, weight=0.30,
                detail="11 of 13 required terms present.",
            ),
            DimensionScore(
                dimension=ScoreDimension.PARSEABILITY, score=100.0, weight=0.25,
                detail="Single column, no tables or text boxes; all sections recovered.",
            ),
            DimensionScore(
                dimension=ScoreDimension.IMPACT_QUANTIFICATION, score=91.0, weight=0.20,
                detail="10 of 11 bullets carry a metric.",
            ),
            DimensionScore(
                dimension=ScoreDimension.SECTION_STRUCTURE, score=100.0, weight=0.10,
                detail="Standard headings recognised.",
            ),
            DimensionScore(
                dimension=ScoreDimension.LENGTH_DENSITY, score=78.0, weight=0.08,
                detail="Single page at 92% fill -- slightly dense.",
            ),
            DimensionScore(
                dimension=ScoreDimension.CONTACT_COMPLETENESS, score=85.0, weight=0.04,
                detail="Phone number absent.",
            ),
            DimensionScore(
                dimension=ScoreDimension.DATE_CONSISTENCY, score=100.0, weight=0.03,
                detail="All ranges parse, no gaps over 6 months.",
            ),
        ],
        findings=[
            ATSFinding(
                dimension=ScoreDimension.KEYWORD_MATCH,
                severity=FindingSeverity.CRITICAL,
                code="missing_required_keyword",
                message="'Kubernetes' appears 4 times in the posting but not on the resume.",
                suggestion="Your notes mention EKS. Surface it as 'Kubernetes (EKS)' so both "
                "the literal term and your actual experience are matched.",
                auto_fixable=True,
            ),
            ATSFinding(
                dimension=ScoreDimension.IMPACT_QUANTIFICATION,
                severity=FindingSeverity.WARNING,
                code="bullet_missing_metric",
                message="One bullet under 'Acme Corp' has no quantified outcome.",
                suggestion="Open the generation agent on this bullet to add a metric.",
                target_bullet_id=_MOCK_ID,
                auto_fixable=True,
            ),
        ],
        keyword_analysis=KeywordAnalysis(
            required_present=["Python", "FastAPI", "MongoDB", "AWS", "Docker", "CI/CD",
                              "REST", "PostgreSQL", "Redis", "Git", "Linux"],
            required_missing=["Kubernetes", "Terraform"],
            preferred_present=["LangChain", "Playwright"],
            preferred_missing=["gRPC"],
        ),
        extraction_confidence=0.98,
        computed_at=utcnow(),
    )


@router.post(
    "/score",
    response_model=ATSScore,
    status_code=status.HTTP_200_OK,
    summary="Score a resume version",
)
async def score_resume(payload: ScoreRequest, user: CurrentUser) -> ATSScore:
    """Score a rendered resume version, optionally against a specific posting.

    TODO: the critical implementation constraint -- score the text extracted
    from the *compiled PDF*, not the resume object or the LaTeX source. Load
    `ResumeVersion.pdf_path`, extract with pdfplumber (or pdftotext), and run
    every dimension against that string.

    Scoring the in-memory model would award a perfect parseability score to a
    document no ATS can read, which is worse than not scoring at all: it tells
    the user their resume is fine when it is silently broken.

    If `pdf_path` is unset, return 409 -- the version has not finished
    rendering, and there is nothing to extract.

    Runs synchronously here because extraction plus scoring is ~200ms once the
    PDF exists. If LLM-based semantic scoring is added later, move this to
    `enqueue_score` and make it a polled job.
    """
    return _mock_score(payload.resume_version_id, str(user.id or _MOCK_ID), payload.job_id)


@router.get(
    "/scores/{version_id}",
    response_model=Page[ATSScore],
    summary="Score history for a version",
)
async def score_history(
    version_id: str, user: CurrentUser, page: PaginationParams
) -> Page[ATSScore]:
    """All scoring runs for one version, newest first.

    TODO: covered by `ix_version_computed`. Exclude `extracted_text` from the
    list projection -- it is up to 40KB per document.
    """
    return Page[ATSScore](
        items=[_mock_score(version_id, str(user.id or _MOCK_ID), None)],
        total=1,
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

    Powers the "did my edit help?" view, which is the feature that makes the
    scorer feel like a tool rather than a verdict.

    TODO: fetch the newest score for each version, then diff by finding `code`
    rather than by prose message -- messages contain interpolated numbers and
    would show spurious changes.
    """
    return ScoreComparison(
        baseline_version_id=baseline_version_id,
        candidate_version_id=candidate_version_id,
        baseline_overall=79.0,
        candidate_overall=87.5,
        resolved_findings=["missing_required_keyword", "weak_action_verb"],
        new_findings=["length_density_high"],
    )


@router.get(
    "/extracted-text/{version_id}",
    summary="What an ATS actually sees",
)
async def extracted_text(version_id: str, user: CurrentUser) -> dict[str, object]:
    """Return the raw text recovered from the compiled PDF.

    Worth building early. Showing someone that their elegant two-column
    template linearizes into scrambled nonsense is more persuasive than any
    score, and it is the feature users screenshot and share.

    TODO: read `ATSScore.extracted_text` from the newest score for this
    version; if none exists, extract on demand and cache it.
    """
    return {
        "version_id": version_id,
        "extraction_confidence": 0.98,
        "text": (
            "ADA LOVELACE\nada@example.com | London, UK\n\n"
            "EXPERIENCE\nSenior Backend Engineer, Acme Corp | Jan 2023 - Present\n"
            "Rebuilt the payment retry pipeline cutting failed-charge rate from 4.2% "
            "to 0.8% across 1.2M monthly transactions using idempotency keys and "
            "exponential backoff in a Redis-backed job queue.\n"
        ),
        "warnings": [],
    }
