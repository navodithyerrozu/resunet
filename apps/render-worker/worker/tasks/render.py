"""The render task: resume version -> PDF on disk.

Contract with the API (`app/services/queue.py::enqueue_render`):
name `render_resume_pdf`, kwargs `version_id`, `user_id`, `template`.

Failure handling deserves a note. A LaTeX failure is almost never transient --
the same source compiled again fails identically -- so this task does not retry
on `LatexCompileError`. Retrying would burn three attempts and delay the error
reaching the user by a minute, for nothing. Infrastructure failures (Mongo
unreachable, disk full) *are* transient and are allowed to propagate so ARQ
retries them. Distinguishing the two is the whole job of the except clauses
below.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from pymongo import ReturnDocument

from worker import db, storage
from worker.compile import LatexCompileError, compile_latex
from worker.context import build_context
from worker.latex import render_template
from worker.models import ResumeVersionRead
from worker.settings import get_settings

logger = structlog.get_logger(__name__)

_ALLOWED_TEMPLATES = frozenset({"ats_classic", "ats_compact", "ats_technical"})


class RenderTaskError(RuntimeError):
    """Permanent failure. Recorded on the job; not retried."""


async def render_resume_pdf(
    ctx: dict[str, Any],
    *,
    version_id: str,
    user_id: str,
    template: str,
) -> dict[str, Any]:
    """Compile a stored resume version to PDF.

    Returns a small result dict that ARQ persists, so the API can read the
    outcome from the job result as well as from the `render_jobs` document.
    """
    log = logger.bind(version_id=version_id, user_id=user_id, template=template)
    settings = get_settings()
    started = datetime.now(UTC)

    await _mark_job(version_id, status="rendering", started_at=started)

    try:
        version = await _load_version(version_id, user_id)

        # The template name reaches this worker through a Redis payload, and is
        # then used to pick a file off disk. Validating against an allowlist
        # rather than sanitising the string is what stops a crafted payload
        # loading `../../../../etc/passwd` through the Jinja loader.
        chosen = template if template in _ALLOWED_TEMPLATES else version.template
        if chosen not in _ALLOWED_TEMPLATES:
            raise RenderTaskError(f"unknown template: {chosen!r}")

        source = render_template(f"{chosen}.tex.j2", build_context(version))
        result = await compile_latex(source, job_name=f"resume-{version.version}")

        if result.page_count and result.page_count > settings.render_max_pages:
            # Not an error. The PDF is valid and the user may have wanted it;
            # they simply need to be told, so this is logged and surfaced in the
            # response rather than failing the job.
            log.warning("render.exceeds_page_budget", pages=result.page_count)

        relative = storage.relative_pdf_path(
            user_id=user_id,
            resume_id=version.resume_id,
            version=version.version,
            sha256=result.pdf_sha256,
        )
        await storage.write_pdf(relative, result.pdf_bytes)

        finished = datetime.now(UTC)
        await db.db()["resume_versions"].update_one(
            {"_id": _oid(version_id)},
            {
                "$set": {
                    "pdf_path": relative,
                    "pdf_sha256": result.pdf_sha256,
                    "page_count": result.page_count,
                    "rendered_at": finished,
                    # The exact source is retained because pdflatex errors are
                    # unintelligible without the input that produced them, and
                    # by the time a user reports a problem the bullets have
                    # usually been edited.
                    "latex_source": source,
                    "updated_at": finished,
                }
            },
        )
        await _mark_job(
            version_id,
            status="succeeded",
            finished_at=finished,
            duration_ms=result.duration_ms,
        )

        # Scoring is chained rather than inlined: it is a separate unit of work
        # with its own failure mode, and a scoring bug must not invalidate a
        # PDF that compiled cleanly.
        await _enqueue_score(ctx, version_id=version_id, user_id=user_id)

        log.info(
            "render.succeeded",
            pages=result.page_count,
            sha256=result.pdf_sha256[:12],
            duration_ms=result.duration_ms,
        )
        return {
            "ok": True,
            "pdf_path": relative,
            "pdf_sha256": result.pdf_sha256,
            "page_count": result.page_count,
            "over_page_budget": bool(
                result.page_count and result.page_count > settings.render_max_pages
            ),
        }

    except LatexCompileError as exc:
        # Permanent: same input, same failure. Swallowed deliberately so ARQ
        # does not retry, with the log tail persisted for the user-facing error.
        log.error("render.latex_failed", error=str(exc), log_tail=exc.log_tail[:400])
        await _mark_job(
            version_id,
            status="failed",
            finished_at=datetime.now(UTC),
            error_message=str(exc),
            latex_log_tail=exc.log_tail,
        )
        return {"ok": False, "error": str(exc), "retryable": False}

    except RenderTaskError as exc:
        log.error("render.permanent_failure", error=str(exc))
        await _mark_job(
            version_id,
            status="failed",
            finished_at=datetime.now(UTC),
            error_message=str(exc),
        )
        return {"ok": False, "error": str(exc), "retryable": False}

    except Exception as exc:
        # Infrastructure. Record it, then re-raise so ARQ's retry applies --
        # a Mongo failover or a full disk is exactly what retries are for.
        log.exception("render.infrastructure_failure")
        await _mark_job(
            version_id,
            status="queued",  # back to queued: a retry is coming
            error_message=f"infrastructure: {exc}",
        )
        raise


async def _load_version(version_id: str, user_id: str) -> ResumeVersionRead:
    """Fetch the version, scoped to its owner.

    `user_id` is in the filter rather than asserted afterwards. The queue is not
    an authenticated boundary, so treating the payload as untrusted and letting
    the query enforce ownership means a malformed or forged payload finds
    nothing instead of rendering someone else's resume.
    """
    doc = await db.db()["resume_versions"].find_one(
        {"_id": _oid(version_id), "user_id": _oid(user_id)}
    )
    if doc is None:
        raise RenderTaskError(f"resume version {version_id} not found for user {user_id}")
    return ResumeVersionRead.model_validate(_stringify_ids(doc))


async def _mark_job(version_id: str, **fields: Any) -> None:
    """Update the tracking `render_jobs` document.

    Best-effort by design: if the status write fails the render itself may
    still have succeeded, and losing progress metadata is preferable to
    discarding a compiled PDF.
    """
    fields["updated_at"] = datetime.now(UTC)
    try:
        await db.db()["render_jobs"].find_one_and_update(
            {"version_id": _oid(version_id)},
            {"$set": fields, "$inc": {"attempts": 1} if "started_at" in fields else {}},
            return_document=ReturnDocument.AFTER,
        )
    except Exception:  # noqa: BLE001 - never let bookkeeping fail a render
        logger.warning("render.job_status_write_failed", version_id=version_id)


async def _enqueue_score(ctx: dict[str, Any], *, version_id: str, user_id: str) -> None:
    """Chain the ATS scoring task onto the same queue."""
    redis = ctx.get("redis")
    if redis is None:
        logger.warning("render.no_redis_in_ctx_skipping_score", version_id=version_id)
        return
    await redis.enqueue_job(
        "score_resume_ats",
        version_id=version_id,
        user_id=user_id,
        _job_id=f"score:{version_id}:generic",
        _queue_name=get_settings().render_queue_name,
    )


def _oid(value: str) -> Any:  # noqa: ANN401 - bson import kept local
    from bson import ObjectId

    return ObjectId(value)


def _stringify_ids(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert ObjectIds to strings so the Pydantic read models accept them."""
    out = dict(doc)
    for key in ("_id", "resume_id", "user_id"):
        if key in out and out[key] is not None:
            out[key] = str(out[key])
    return out
