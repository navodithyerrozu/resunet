"""The submission task.

Ordering here is load-bearing, so it is worth stating the sequence and why each
guard precedes the next:

1. **Circuit check** -- before a browser is launched, because launching costs
   300MB and two seconds for a board we already know is refusing us.
2. **Pacing check** -- defer rather than sleep, so the worker slot returns to
   the pool for other boards.
3. **Terminal-state check** -- an application already submitted must never be
   submitted twice. ARQ delivers at-least-once, so this is a real path, not a
   theoretical one.
4. **Fill, then pause for approval** -- the form is complete and screenshotted
   *before* anything irreversible happens, so the human approving it is looking
   at what will actually be sent.
5. **Submit, then verify** -- and an unverified submit is `NEEDS_HUMAN`, never
   `SUCCESS`.

The step that deserves the most suspicion is 5. If `submit()` succeeds and
`verify_submitted()` finds nothing, the truthful answer is that we do not know
whether the application landed. Recording success would produce a user who
believes they applied and did not; recording failure invites a duplicate
submission. So it is escalated to a human with a screenshot attached.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from playwright.async_api import Browser

from worker import browser as browser_mod
from worker import db, detect, retry
from worker.adapters import AdapterError, UnmappedFieldError, resolve_adapter
from worker.adapters.base import ApplicantDetails, SubmissionPayload
from worker.crypto import CredentialError, decrypt_login, redact
from worker.settings import get_settings

logger = structlog.get_logger(__name__)

_TERMINAL_STATUSES = frozenset({"submitted", "rejected", "withdrawn", "offer", "hired"})


async def submit_application(
    ctx: dict[str, Any],
    application_id: str,
    *,
    user_id: str | None = None,
    approved: bool = False,
) -> dict[str, Any]:
    """Submit one job application. Registered as an ARQ task.

    `user_id` is redundant with the application document, which already carries
    the owner -- and it is passed anyway, so the two can be compared. A queue is
    not an authenticated boundary: payloads are replayable, and anything able to
    write to Redis can enqueue an arbitrary `application_id`. Checking the
    enqueuer's view of the owner against the stored one costs one comparison and
    turns a silent cross-tenant submission into a refusal.

    `approved=True` is set only by the API's explicit approval endpoint, and is
    the *one* thing that bypasses the approval gate. It re-runs the whole
    pipeline from navigation rather than resuming a held browser: a context held
    open across a human wait is 400MB hostage to someone's lunch break, and it
    dies on the next deploy regardless. The cost is a second visit to the board,
    which is why `mark_board_hit` pacing applies to it identically.

    Returns a result dict rather than raising on business failures: an ARQ
    exception triggers ARQ's own retry, which knows nothing about bot walls or
    board pacing. Retry decisions belong to `retry.py`, so failures are returned
    and re-enqueued deliberately.
    """
    settings = get_settings()
    database = db.db()
    redis = ctx["redis"]
    attempt_number = int(ctx.get("job_try", 1))

    log = logger.bind(
        application_id=application_id, attempt=attempt_number, approved=approved
    )

    application = await database.applications.find_one({"_id": application_id})
    if application is None:
        log.error("submit.application_not_found")
        return _result("permanent_failure", "application not found")

    if application.get("status") in _TERMINAL_STATUSES:
        # At-least-once delivery means this is reachable on a redelivered job.
        log.info("submit.already_terminal", status=application.get("status"))
        return _result("success", "already in a terminal state; nothing to do")

    if user_id is not None and application.get("user_id") != user_id:
        # Refuse rather than proceed on the stored owner. If these disagree, the
        # queue payload does not describe reality, and the next irreversible
        # action would be taken on behalf of the wrong person.
        log.error(
            "submit.owner_mismatch",
            detail="enqueued user_id does not match the application's owner; "
            "refusing to submit",
        )
        return _result("permanent_failure", "application does not belong to the requesting user")

    job = await database.jobs.find_one({"_id": application["job_id"]})
    if job is None:
        return _result("permanent_failure", "job posting record missing")

    board = (job.get("source") or {}).get("board") or "generic"
    job_url = (job.get("source") or {}).get("url") or job.get("url")
    if not job_url:
        return _result("permanent_failure", "job has no application URL")

    # 1. Circuit breaker, before spending anything.
    try:
        await retry.check_circuit(redis, board)
    except retry.CircuitOpenError as exc:
        log.warning("submit.circuit_open", board=board, retry_after=exc.retry_after_seconds)
        await _record_attempt(
            database, application_id, attempt_number,
            outcome="retryable_failure", step="preflight",
            error=f"board circuit open; deferred {exc.retry_after_seconds:.0f}s",
        )
        return _result("deferred", str(exc), defer_seconds=exc.retry_after_seconds)

    # 2. Per-board pacing.
    wait = await retry.seconds_until_board_available(redis, board)
    if wait > 0:
        log.info("submit.paced", board=board, defer_seconds=wait)
        return _result("deferred", f"board pacing: {wait:.0f}s", defer_seconds=wait)

    version = await database.resume_versions.find_one({"_id": application["resume_version_id"]})
    if version is None or not version.get("pdf_path"):
        return _result("permanent_failure", "no rendered PDF for this resume version")

    pdf_path = Path(version["pdf_path"])
    if not pdf_path.exists():
        # The render succeeded once but the artifact is gone -- a volume was
        # recreated or a retention job reaped it. Re-rendering is the fix, so
        # this is retryable at a higher level, not here.
        return _result("permanent_failure", "rendered PDF is missing from storage")

    profile = await database.career_profiles.find_one({"user_id": application["user_id"]})
    user = await database.users.find_one({"_id": application["user_id"]})
    if profile is None or user is None:
        return _result("permanent_failure", "user profile is incomplete")

    payload = SubmissionPayload(
        application_id=application_id,
        job_url=job_url,
        applicant=_applicant_from(profile, user),
        resume_pdf_path=pdf_path,
        cover_letter_text=application.get("cover_letter_text"),
        answers=application.get("screening_answers") or {},
    )

    adapter = resolve_adapter(job_url)
    # An adapter may raise the approval bar; it may never lower it. `approved`
    # satisfies the gate rather than disabling it -- the human has looked.
    require_approval = not approved and (
        settings.automation_require_approval or adapter.always_require_approval
    )

    secrets: list[str] = []
    login = None
    if adapter.requires_login:
        credential = await database.board_credentials.find_one(
            {"user_id": application["user_id"], "board": board, "status": "active"}
        )
        if credential is None:
            return _result("needs_human", f"no stored credentials for board {board!r}")
        try:
            login = decrypt_login(credential["encrypted_blob"])
        except CredentialError as exc:
            log.error("submit.credentials_unusable")
            return _result("needs_human", str(exc))
        secrets = [login.password, login.username]

    await retry.mark_board_hit(redis, board)
    await _set_status(database, application_id, "submitting")

    browser: Browser = ctx["browser"]
    step = "preflight"
    screenshot_path: Path | None = None

    try:
        async with browser_mod.browser_session(
            browser, application_id=application_id
        ) as session:
            step = "open_application"
            await adapter.open_application(session, payload)

            blocked = await detect.detect_block(session.page)
            if blocked is not None:
                return await _handle_block(
                    database, redis, session, application_id, attempt_number, board, blocked
                )

            if adapter.requires_login and login is not None:
                step = "login"
                # Tracing starts only after credentials are entered: a trace with
                # DOM snapshots records input values, so tracing through a login
                # writes the user's password into a zip file.
                await adapter.login(session, login)
                blocked = await detect.detect_block(session.page)
                if blocked is not None:
                    return await _handle_block(
                        database, redis, session, application_id,
                        attempt_number, board, blocked,
                    )

            await browser_mod.start_trace(session)

            step = "fill_fields"
            mappings = await adapter.fill_fields(session, payload)

            step = "upload_resume"
            await adapter.upload_resume(session, payload.resume_pdf_path)

            step = "answer_questions"
            mappings += await adapter.answer_questions(session, payload.answers)

            # Evidence captured with the form filled and before submitting. This
            # is the image a human approves against.
            screenshot_path = await browser_mod.capture_screenshot(
                session.page,
                settings.trace_storage_dir / f"{application_id}-prefill.png",
            )

            if approved:
                # The approval was given against an earlier fill of this form. If
                # the form has changed since, that approval no longer describes
                # what is about to be sent, so it is void. This is the whole
                # reason the fill is fingerprinted rather than just flagged.
                step = "await_approval"
                previous = _approved_fingerprint(application)
                current = _fingerprint(mappings)
                if previous is not None and previous != current:
                    await _record_attempt(
                        database, application_id, attempt_number,
                        outcome="needs_human", step=step,
                        error="the form changed since it was approved; re-approval is "
                              "required before submitting",
                        screenshot_path=str(screenshot_path) if screenshot_path else None,
                        field_mappings=[_mapping_doc(m) for m in mappings],
                        final_url=session.page.url,
                    )
                    await _set_status(database, application_id, "awaiting_approval")
                    log.warning(
                        "submit.approval_stale",
                        added=sorted(current - previous),
                        removed=sorted(previous - current),
                    )
                    return _result("needs_human", "approval is stale; form changed")

            if require_approval:
                step = "await_approval"
                await _record_attempt(
                    database, application_id, attempt_number,
                    outcome="needs_human", step=step,
                    error=None,
                    screenshot_path=str(screenshot_path) if screenshot_path else None,
                    field_mappings=[_mapping_doc(m) for m in mappings],
                    final_url=session.page.url,
                )
                await _set_status(database, application_id, "awaiting_approval")
                log.info(
                    "submit.awaiting_approval",
                    board=board,
                    adapter=adapter.board,
                    field_count=len(mappings),
                )
                # The browser closes here. Approval arrives out-of-band and
                # enqueues a fresh job with `approved=True`, which replays this
                # pipeline from navigation.
                return _result("needs_human", "form filled; awaiting human approval")

            step = "submit"
            await adapter.submit(session)

            step = "verify_submitted"
            evidence = await adapter.verify_submitted(session.page)
            screenshot_path = await browser_mod.capture_screenshot(
                session.page,
                settings.trace_storage_dir / f"{application_id}-result.png",
            )

            if evidence.confirmation_text is None:
                # See the module docstring: unknown is not success.
                await retry.record_board_failure(redis, board)
                await _record_attempt(
                    database, application_id, attempt_number,
                    outcome="needs_human", step=step,
                    error="submitted but no confirmation was found; verify manually "
                          "before resubmitting to avoid a duplicate application",
                    screenshot_path=str(screenshot_path) if screenshot_path else None,
                    final_url=evidence.final_url,
                )
                await _set_status(database, application_id, "needs_review")
                return _result("needs_human", "submission unconfirmed")

            await retry.record_board_success(redis, board)
            await _record_attempt(
                database, application_id, attempt_number,
                outcome="success", step=step, error=None,
                screenshot_path=str(screenshot_path) if screenshot_path else None,
                field_mappings=[_mapping_doc(m) for m in mappings],
                final_url=evidence.final_url,
                confirmation_text=evidence.confirmation_text,
            )
            await _set_status(database, application_id, "submitted", submitted=True)
            log.info("submit.success", board=board, adapter=adapter.board)
            return _result("success", "application submitted")

    except UnmappedFieldError as exc:
        # Not retryable: identical logic against an identical form fails
        # identically. A human resolves it in seconds.
        await _record_attempt(
            database, application_id, attempt_number,
            outcome="needs_human", step=exc.step, error=str(exc),
            screenshot_path=str(screenshot_path) if screenshot_path else None,
        )
        await _set_status(database, application_id, "needs_review")
        return _result("needs_human", str(exc))

    except AdapterError as exc:
        message = redact(str(exc), secrets)
        outcome = "retryable_failure" if exc.retryable else "permanent_failure"
        await retry.record_board_failure(redis, board)
        await _record_attempt(
            database, application_id, attempt_number,
            outcome=outcome, step=exc.step, error=message,
            screenshot_path=str(screenshot_path) if screenshot_path else None,
        )
        log.warning("submit.adapter_error", step=exc.step, retryable=exc.retryable)

        if exc.retryable and retry.should_retry(
            retry.AttemptOutcome.RETRYABLE_FAILURE, attempt_number
        ):
            delay = retry.backoff_seconds(attempt_number)
            await _set_status(database, application_id, "queued")
            return _result("deferred", message, defer_seconds=delay)

        await _set_status(database, application_id, "failed")
        return _result(outcome, message)

    except Exception as exc:  # noqa: BLE001 - last line of defence
        message = redact(str(exc), secrets)
        await retry.record_board_failure(redis, board)
        # `exc_info` and not the message alone: an unexpected exception here is a
        # bug in our code, and the traceback is the only way to find it.
        log.exception("submit.unexpected_error", step=step)
        await _record_attempt(
            database, application_id, attempt_number,
            outcome="retryable_failure", step=step, error=message[:2000],
            screenshot_path=str(screenshot_path) if screenshot_path else None,
        )
        if retry.should_retry(retry.AttemptOutcome.RETRYABLE_FAILURE, attempt_number):
            await _set_status(database, application_id, "queued")
            return _result("deferred", message, defer_seconds=retry.backoff_seconds(attempt_number))
        await _set_status(database, application_id, "failed")
        return _result("retryable_failure", message)


async def _handle_block(
    database: Any,
    redis: Any,
    session: browser_mod.BrowserSession,
    application_id: str,
    attempt_number: int,
    board: str,
    reason: detect.BlockReason,
) -> dict[str, Any]:
    """Record a block and stop. Never schedules a retry.

    The whole point: retrying escalates a soft challenge into a range ban on a
    shared egress IP. Opening the circuit protects the rest of the queue from
    walking into the same wall.
    """
    settings = get_settings()
    screenshot = await browser_mod.capture_screenshot(
        session.page, settings.trace_storage_dir / f"{application_id}-blocked.png"
    )
    await retry.record_board_failure(redis, board)
    await _record_attempt(
        database, application_id, attempt_number,
        outcome="blocked", step="detect", error=f"blocked: {reason.value}",
        screenshot_path=str(screenshot) if screenshot else None,
        block_reason=reason.value,
        final_url=session.page.url,
    )
    await _set_status(database, application_id, "blocked")
    logger.error(
        "submit.blocked",
        application_id=application_id,
        board=board,
        reason=reason.value,
        detail="Terminal by design. Not retrying -- repeated attempts turn a "
        "challenge into an IP-range ban affecting every user.",
    )
    return _result("blocked", f"blocked by board: {reason.value}")


def _applicant_from(profile: dict[str, Any], user: dict[str, Any]) -> ApplicantDetails:
    contact = profile.get("contact") or {}
    full_name = contact.get("full_name") or user.get("full_name") or ""
    first, _, last = full_name.partition(" ")
    return ApplicantDetails(
        full_name=full_name,
        first_name=first or full_name,
        last_name=last or "",
        email=contact.get("email") or user.get("email") or "",
        phone=contact.get("phone"),
        location=contact.get("location"),
        linkedin_url=contact.get("linkedin_url"),
        github_url=contact.get("github_url"),
        portfolio_url=contact.get("portfolio_url"),
        work_authorized=profile.get("work_authorized"),
        requires_sponsorship=profile.get("requires_sponsorship"),
    )


def _mapping_doc(mapping: Any) -> dict[str, Any]:
    return {
        "selector": mapping.selector,
        "label": mapping.label,
        "value_preview": mapping.value_preview,
        "strategy": mapping.strategy,
        "confidence": mapping.confidence,
    }


def _fingerprint(mappings: list[Any]) -> frozenset[tuple[str, str]]:
    """A comparable identity for one fill of a form.

    `(label, value_preview)` and not the selector: selectors churn on every
    front-end deploy without the form meaning anything different, whereas a
    changed label or a field that appeared or vanished is exactly the change that
    invalidates a human's approval. Confidence is excluded for the same reason --
    a heuristic re-scoring the same match is not a change to the form.
    """
    return frozenset((m.label, m.value_preview) for m in mappings)


def _approved_fingerprint(application: dict[str, Any]) -> frozenset[tuple[str, str]] | None:
    """The fill that was approved, or None if there is nothing to compare to.

    None means "cannot verify", and the caller treats it as permission to proceed
    rather than as a mismatch. That is the right call: the alternative deadlocks
    an application whose attempt history was trimmed, and the human-approval step
    itself has already happened.
    """
    for attempt in reversed(application.get("attempts") or []):
        if attempt.get("failed_step") == "await_approval":
            mappings = attempt.get("field_mappings") or []
            if not mappings:
                return None
            return frozenset(
                (m.get("label") or "", m.get("value_preview") or "") for m in mappings
            )
    return None


async def _record_attempt(
    database: Any,
    application_id: str,
    attempt_number: int,
    *,
    outcome: str,
    step: str,
    error: str | None,
    screenshot_path: str | None = None,
    trace_path: str | None = None,
    block_reason: str | None = None,
    final_url: str | None = None,
    confirmation_text: str | None = None,
    field_mappings: list[dict[str, Any]] | None = None,
) -> None:
    """Append an attempt to the application's embedded history.

    `$push` rather than `$set`: the history of what was tried is the audit trail,
    and overwriting it destroys the record of a first attempt that got blocked.
    """
    attempt = {
        "attempt_number": attempt_number,
        "outcome": outcome,
        "failed_step": None if outcome == "success" else step,
        "error_message": error,
        "screenshot_path": screenshot_path,
        "trace_path": trace_path,
        "block_reason": block_reason,
        "final_url": final_url,
        "confirmation_text": confirmation_text,
        "field_mappings": field_mappings or [],
        "started_at": datetime.now(UTC).isoformat(),
    }
    await database.applications.update_one(
        {"_id": application_id},
        {"$push": {"attempts": attempt}, "$set": {"updated_at": datetime.now(UTC)}},
    )


async def _set_status(
    database: Any, application_id: str, status: str, *, submitted: bool = False
) -> None:
    update: dict[str, Any] = {"status": status, "updated_at": datetime.now(UTC)}
    if submitted:
        update["submitted_at"] = datetime.now(UTC)
    await database.applications.update_one({"_id": application_id}, {"$set": update})


def _result(
    outcome: str, message: str, *, defer_seconds: float | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {"outcome": outcome, "message": message}
    if defer_seconds is not None:
        result["defer_seconds"] = defer_seconds
    return result
