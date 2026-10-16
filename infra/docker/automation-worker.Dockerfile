# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# Playwright automation worker.
#
# Uses Microsoft's official Playwright image so the browser build and the
# system libraries it needs are already version-matched. Pinning the tag to
# the same version as the `playwright` pip dependency matters: a mismatch
# between the Python client and the bundled browser fails at runtime, not at
# build time.
# ---------------------------------------------------------------------------
FROM mcr.microsoft.com/playwright/python:v1.47.0-noble

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/automation-worker

COPY apps/automation-worker/pyproject.toml ./
COPY apps/automation-worker/worker ./worker
RUN pip install --upgrade pip && pip install .

# The base image ships a `pwuser` with the browser sandbox configured.
RUN groupadd --system --gid 10003 renderdata \
    && usermod --append --groups renderdata pwuser \
    && mkdir -p /srv/storage/renders /srv/storage/traces \
    && chown -R pwuser:renderdata /srv \
    && chmod 2775 /srv/storage/renders
USER pwuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD ["arq", "worker.main.WorkerSettings", "--check"]

CMD ["arq", "worker.main.WorkerSettings"]
