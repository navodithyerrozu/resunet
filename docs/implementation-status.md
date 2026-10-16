# ResuNet implementation status

Branch: `cursor/career-profile-persistence` (starting commit `e8cd111`). Changes
remain uncommitted. The pre-existing untracked `pnpm-lock.yaml` is preserved.

## Baseline and quality gates

- **TESTED** — API suite: 117 passed under Python 3.12.14 after adding an
  authenticated profile-to-resume HTTP journey and an Anthropic-only provider
  config regression test; the same run emitted one upstream Argon2 warning.
- **TESTED** — Render-worker suite: 45 passed, 1 symlink test skipped because
  Windows denied symlink creation; includes LaTeX timeout cleanup and PDF path
  traversal/read-write tests. Worker Ruff and mypy pass under
  Python 3.12 after cleanup of new toolchain findings.
- **TESTED** — Automation-worker suite: 59 passed under Python 3.12, including
  exact-host authorization, network-request blocking, approval guard, atomic
  submission claim, and deferred-retry scheduling coverage; worker Ruff and
  mypy pass. Browser fingerprint spoofing and human-mimicry were removed. API
  and both worker suites previously passed with the Python 3.12 environment
  and current resolved dependencies used by the CI job. The automation worker
  wheel also builds successfully after its latest code changes.
- **TESTED** — Queue contract suite: 9 passed.
- **TESTED** — Shared and web TypeScript checks passed; shared offline API
  contract check passed; web ESLint passed on `src`.
- **TESTED** — API mypy and repository-wide Ruff (`app tests`) pass. Worker Ruff
  and mypy checks are included in the CI workflow.
- **IN PROGRESS** — Next production build compiled, typechecked, and
  generated pages after enabling standalone output, but final tracing failed
  because Windows could not create pnpm symlinks in `.next/standalone` (EPERM).
  Reproduced on this host: Next.js compilation, type-checking, and all 19 static
  pages passed; the failure is at standalone file tracing. This host has Node
  24/pnpm 11 rather than CI's Node 20/pnpm 9.12. A Linux Docker/CI build is
  still required to verify the production image.
- **BLOCKED** — Docker-backed verification: Docker Desktop engine unavailable.
- **BLOCKED** — pnpm workspace commands: pnpm attempts a store symlink and
  fails with EPERM. The lockfile has not been edited.

## Product implementation

- **TESTED** — Career-profile authenticated editor, same-origin BFF, save and
  reload states, stable profile entry IDs, and navigation. API profile route
  tests now perform PUT followed by a separate GET and verify the saved contact,
  headline, education, and skills; ownership and stable entry IDs are also
  covered. Profile replacement and
  experience deletion now archive dependent bullets in the same Mongo
  transaction.
- **IN PROGRESS** — Authentication lifecycle. Registration creates the user
  and profile transactionally; login, refresh validation, and token-version
  revocation exist. Logout's Mongo filter now uses an `ObjectId`. Registration
  and login now share a configurable Redis fixed-window limit per normalized,
  hashed email (10/minute by default). Email-keyed limits avoid treating all
  users behind the same-origin web BFF as one client. Endpoint tests cover generic
  invalid-credential responses, expired and revoked refresh tokens, inactive
  accounts, duplicate registration handling, and logout's token-version increment.
  The web logout route now refreshes an expired access token before revocation;
  if server revocation fails, it clears local cookies but returns a warning and
  tells the user that other sessions may remain active rather than claiming
  logout fully succeeded.
  Production configuration rejects loopback database/cache URLs. Live Mongo/Redis
  behavior remains unverified.
- **TESTED** — Resume create/list/get/update/archive and owner-scoped version
  history. BSON references are stored as ObjectIds; section updates validate
  owned profile entries and approved bullets. An additional authenticated HTTP
  journey now verifies profile-to-resume creation, reload, update, and archive.
- **IN PROGRESS** — LangGraph generation start/turn routes now invoke the
  checkpointed graph, scope threads to the owner and a real resume entry, pass
  job keywords, and enforce an hourly Redis request cap. API response mapping
  tests pass. Finalized proposals receive stable persisted IDs; users can
  accept compliant bullets, revise flagged bullets, or reject them. Accepted
  bullets attach to the matching resume section. Finalizer and API tests cover
  BSON relationships, replay-safe idempotent persistence, decisions, validation,
  and attachment. The installed
  Mongo saver API mismatch was fixed using its async executor methods. The
  current thread and decision state restore after a page reload. A live
  model/Mongo/Redis run was not performed. Anthropic is the sole configured
  provider because the model factory only constructs `ChatAnthropic`; unused
  OpenAI settings and Compose values were removed rather than implying support.
- **IN PROGRESS** — Render API freezes a resume version and queues the existing
  worker; job status and owner-scoped history are persisted and polled. Owner-
  checked PDF streaming and a same-origin preview route exist; render polling
  now has a timeout. Worker date fields are supported. Transient worker failures
  now use ARQ's explicit delayed retry signal, and the final configured attempt
  persists a failed state rather than leaving the job queued indefinitely.
  ATS queue failure is now isolated from a completed PDF render, so the render
  remains succeeded when the separate scoring enqueue is unavailable. Focused
  retry-state and queue-isolation tests pass, as do worker Ruff and mypy checks.
  ATS score writes now use one partial-indexed idempotency key per immutable
  version, job, and engine version, and transient scoring failures use the same
  bounded ARQ retry policy. Shared logs now omit compiler logs and raw PDF parse
  exceptions, which can contain resume text. API and browser-automation
  unhandled-error logs now record exception classes without serializing raw
  traces; redacted details remain in owner-scoped attempt records where needed.
  Detailed render diagnostics remain in the owner-scoped job record. The suite
  ran outside the Windows sandbox because asyncio startup stalls inside it. No
  real Mongo/Redis/LaTeX render was run.
- **TESTED** — ATS routes queue scoring against rendered versions and retrieve
  persisted score history, comparisons, and extraction results. The render
  worker reads the current parsed-keyword schema and updates latest score.
  Testing used doubles; no live PDF/worker integration was run.
- **IN PROGRESS** — Jobs now accepts pasted posting text, stores/deduplicates
  records, and saves per-user deterministic profile matches. Matching only
  scores known terms literally present in the posting and profile; semantic
  similarity is not computed. The Jobs screen and same-origin handler exist.
  Focused parser/match and route tests pass. Mongo aggregation pipelines have
  not been exercised against a live MongoDB; no crawling or external job-board
  integrations are configured.
- **IN PROGRESS** — Applications now supports owner-scoped create/list/detail,
  deduplication, funnel counts, status transitions with events, safe retry and
  human approval queue paths. The UI creates manual drafts from a saved open
  job and rendered resume; it also exposes stage updates. API tests cover draft
  persistence, deduplication, and refusing automation for ResuNet's manually
  pasted jobs. The automation worker atomically claims queued applications to
  prevent concurrent deliveries from opening duplicate browser sessions,
  reschedules its bounded safe retries, and sends uncertain submit/verification
  outcomes to manual review. Approval now fails closed when the prior reviewed
  field evidence is missing or changed. Worker tests cover the claim and retry
  queue contract. Approval and retry APIs persist `queued` before dispatch,
  eliminating a race where a fast worker could see the old state and discard
  its job; queue failures restore the safe prior state with an event. Route
  tests cover dispatch ordering and approval rollback. Queue/worker integration
  is still unverified against Redis and MongoDB. No real external submissions
  have been performed.
- **IN PROGRESS** — Frontend/BFF integration now covers profile, resume render,
  ATS, job intake/matching, and application draft/stage tracking. Generation
  proposal decisions, accepted-bullet attachment, and thread restore after
  reload are connected. Resume archive failures now show API error messages or
  a recoverable connection message instead of being silently ignored. Live
  infrastructure verification and the complete production journey remain.
- **IN PROGRESS** — Added [operations.md](operations.md) documenting the
  Compose development setup, non-destructive stop/recovery, and known limits.
  Added `.github/workflows/ci.yml` for web checks, API and worker suites, the
  queue contract, generated API contract check, Python lint, and Python mypy
  checks. The workflow uses
  `--frozen-lockfile`; the preserved pnpm lockfile is currently untracked and
  must be included in the eventual commit for CI checkout to install
  deterministically. Added a standalone Next.js production image,
  a production-only API image, and a Compose stack for app services backed by
  externally managed MongoDB/Redis. Compose now passes each service only its
  required secrets and points API/render/automation storage settings at the same
  shared volume; a config checker enforces both invariants in CI. Earlier API
  and worker wheel builds verified package discovery; the automation worker's
  updated wheel now also builds and contains its expected worker modules. Its
  Docker image still needs a fresh build.
  The workflow has not run on GitHub. Worker health checks now read ARQ's Redis sentinel, and shared
  render-volume group access is configured. Production Compose requires exact
  application-host allowlisting and explicitly keeps human approval enabled.
  Browser fingerprint spoofing and human-mimicry were removed; requests to
  non-allowlisted hosts are refused. Docker Compose
  config checks pass; the engine itself remains
  unavailable.
  Backup/restore and rollback principles are now documented in
  [operations.md](operations.md). A configured backup provider and restore
  drill, immutable image release process, hosted deployment, and Docker-backed
  verification remain.

## Latest evidence and limits

- API test command: `python -m pytest -q` — **117 passed** on Python 3.12.14
  (one upstream Argon2 deprecation warning).
- New generation response tests cover graph interruptions and completed proposal
  mapping. Mongo saver import/interface was verified against the installed
  package; no database connection was exercised.
- API repository-wide Ruff and mypy — **passed**.
- Fresh final checks in this workspace: Python Ruff, Compose secret/storage
  checker, queue contract (**9 passed**), and `git diff --check` — **passed**.
- Fresh API and worker suite reruns outside the Windows socket restriction:
  API **117 passed**, render worker **45 passed / 1 skipped**, and automation
  worker **59 passed**, all on Python 3.12.14. API emitted one upstream
  `argon2.__version__` deprecation warning; all tests passed. Fresh Ruff and
  mypy checks also passed for all three Python packages (API 50 files, render
  worker 13 files, automation worker 16 files).
- Shared and web `tsc --noEmit` — **passed**.
- Web TypeScript and ESLint on `src` — **passed** in the latest direct rerun.
  Shared package TypeScript check — **passed**. The installed host runtime is
  Node 24/pnpm 11; CI uses Node 20/pnpm 9.12, whose exact run remains pending.
  The ordinary pnpm workspace commands remain blocked by denied store symlink
  creation.
- Shared offline generated-contract check — **passed**.
- Compose secret/storage/host-isolation checker — **passed** without requiring
  a running Docker engine. The updated automation worker wheel built and
  includes its submit, adapter, and browser modules.
- Web ESLint on changed application files — **passed**. Running ESLint over the entire app directory
  included generated `.next` output and was not the repository's lint script.
- Windows sandboxed API tests can stall on local socket creation; the successful
  suite ran outside the sandbox. No test database was reset.
- API, render-worker, automation-worker, and queue-contract suites all pass under
  Python 3.12; current Ruff and mypy checks pass for all three Python packages.
- The latest web production build compiled, typechecked, and rendered pages, but
  standalone tracing stopped when Windows denied pnpm symlink creation. Web
  TypeScript and ESLint pass. Production Compose validation passes with safe
  placeholder environment values; Docker daemon startup/build remains unverified.
- The automation worker wheel was freshly built with Python 3.13 and its package
  contents checked; the automation worker tests, mypy, and Ruff remain the
  stronger runtime/source checks. Docker image packaging remains unverified.
- No live MongoDB, Redis, LLM provider, job board, deployment, or paid model call
  was used.
- Removed the unused `NEXT_PUBLIC_API_BASE_URL` from Compose and the environment
  example; FastAPI remains a server-side-only origin.
- No commits, pushes, merges, deployments, external submissions, or lockfile
  edits were made.
