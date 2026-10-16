# ResuNet local operations

This repository currently provides a **development stack**, not a production
deployment target. `docker-compose.yml` runs MongoDB as a single-node replica
set, Redis, the FastAPI API, both workers, and the Next.js development server.
`infra/docker/web.Dockerfile` starts `pnpm dev`. A separate production compose
file and standalone Next.js runtime image are provided; they expect a
production Mongo replica set and Redis to be managed externally. No deployment
target has been configured.

## First local run

1. Install Docker Desktop with Compose support, Node.js with pnpm 9, and Python
   3.12. Start Docker Desktop.
2. Copy `.env.example` to `.env` and set a private `JWT_SECRET_KEY`. Add the
   Anthropic key only if you intend to use generation. Keep `.env` out of source
   control.
3. From the repository root, run:

   ```sh
   docker compose up --build -d
   docker compose ps
   docker compose exec api python -m app.db.indexes
   ```

4. Open `http://localhost:3000`. The API health endpoint is
   `http://localhost:8000/health/live`.

Compose waits for the Mongo replica-set health check and Redis ping before
starting the API and workers. The web server uses `http://api:8000` over the
Compose network. Compose passes each service only its required settings. The API
receives empty automation credential and proxy values; only the automation
worker receives those secrets. The web container receives only its internal API
origin. API and worker render settings point to the shared `/srv/storage/renders`
volume, with matching group access for the separate container users.

## Stop and recover

- Stop services while preserving local data: `docker compose down`.
- Start them again: `docker compose up -d`.
- Inspect a service: `docker compose logs --tail=100 api` (replace `api` with
  `mongo`, `redis`, `web`, `render-worker`, or `automation-worker`).
- Rebuild after dependency or image changes: `docker compose up --build -d`.

Do not use `docker compose down -v` unless you intentionally want to delete the
MongoDB, Redis, rendered-PDF, browser-trace, and web dependency volumes.

## Production container stack

`docker-compose.production.yml` starts only ResuNet application services and
does not expose the API port. Set `MONGODB_URI` to a reachable replica set,
`REDIS_URI`, a unique `JWT_SECRET_KEY`, `CORS_ORIGINS` to the public web origin,
and `CREDENTIAL_ENCRYPTION_KEY` for the automation worker in the deployment
environment's secret store, and `AUTOMATION_ALLOWED_HOSTS` as a comma-separated
list of exact hostnames the authorized workflow may contact, including form
frames, submission APIs, and required assets. Production automation refuses all
other hosts and requires HTTPS. `AUTOMATION_PROXY_URL`
is optional for approved organizational egress; do not use it to evade site
blocks or access controls. The worker always requires human approval before
submission. Configure a hostname only when the workflow is explicitly authorized
by the relevant site or organization; an allowlist entry alone is not proof of
permission. CAPTCHA, MFA, and access-control blocks stop the workflow and must
not be bypassed. Anthropic is the only configured generation provider.
`ANTHROPIC_API_KEY` is optional until AI generation is enabled; OpenAI is not
supported by the current model factory and has no environment setting.
Apply database indexes before routing traffic, then build and start:

```sh
docker compose -f docker-compose.production.yml run --rm api python -m app.db.indexes
docker compose -f docker-compose.production.yml up --build -d
```

The production Compose configuration, images, and startup sequence still need
verification on a Linux Docker host with production-like MongoDB and Redis.
Worker images use ARQ's Redis health sentinel for container health checks. The
shared render volume uses a common group so the worker can write artifacts and
the API can serve them. This is a container deployment baseline, not a complete
hosted deployment plan.

Render jobs retry transient infrastructure failures up to `RENDER_MAX_TRIES`
times, waiting `RENDER_RETRY_DELAY_SECONDS` between attempts (defaults: 2 and
10 seconds). The worker persists a failed render after the final attempt. ATS
scoring is queued separately; an ATS queue outage does not invalidate a PDF
that has already rendered successfully.

## Backup, recovery, and release rollback

Before accepting production data, choose and document a backup owner and
retention period. Use the MongoDB provider's replica-set-aware backup or
point-in-time recovery feature for the `resunet` database, and separately back
up the `render_storage` volume. MongoDB stores the render metadata while the
volume stores PDF bytes; a database backup alone cannot recover the PDFs. Treat
automation traces as sensitive diagnostic data and apply a short, explicit
retention policy. Do not assume Redis queue contents are a durable backup:
MongoDB application/job records are the recovery source of truth.

Before relying on a backup, restore it into an isolated environment and verify
that the application can read profiles, resumes, versions, applications, and
their corresponding PDF files. Run `python -m app.db.indexes` against the
restored database to ensure required indexes exist. Keep this restore exercise
away from production credentials and endpoints. The current repository does
not configure a backup provider, schedule backups, or automate restore drills.

For an incident, stop incoming traffic first. Pause the API and workers before
reconciling incomplete queued work so workers cannot continue changing records
or files during recovery. Restore MongoDB and the render volume from matching
recovery points when both are needed. Start MongoDB/Redis, apply indexes, start
the API and workers, and inspect readiness and persisted job states before
reopening traffic. Redis queues may contain stale or missing messages after a
restore; reconcile queued/running records against actual artifacts and requeue
only through an authorized operational procedure. Do not blindly replay
automation submissions.

For an application rollback, retain the exact previously verified application
image digests and deployment configuration for each release. Route traffic away
from the release, restore those image digests, then verify readiness and a
read-only user workflow before reopening traffic. Database changes must remain
backward compatible with the prior release; index creation in this project is
explicit and additive, but there is no schema migration/rollback framework.
Never roll back application data automatically with an image rollback. A data
restore is a separate recovery decision because it can discard newer writes.

The current Compose file builds images from source and does not define an image
registry, immutable release tags, secret manager, backup schedule, or traffic
switching mechanism. Those deployment-specific pieces must be configured and
tested by the hosting environment before this procedure can be used as a
production runbook.

## Current operational limits

- Live setup and recovery were not verified in this environment because the
  Docker engine is unavailable.
- Backup, restore, and rollback principles are documented above, but a backup
  provider, restore drill, immutable image release process, and hosted deployment
  target are not configured. A CI workflow is configured, but it has not been
  executed by GitHub Actions from this checkout. Its frozen pnpm install
  requires the preserved `pnpm-lock.yaml` to be included in the commit.
- Rendering and applications require MongoDB, Redis, and their respective
  workers. A queued job is not evidence that the worker completed it.
- Generation is Anthropic-only and needs `ANTHROPIC_API_KEY`. Ordinary tests use
  doubles and should not make paid provider calls.
- Job intake accepts user-pasted descriptions. It does not crawl sites or
  configure external job-board integrations.
- ResuNet does not make external job submissions in tests.
