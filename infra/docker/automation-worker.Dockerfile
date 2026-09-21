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
RUN pip install --upgrade pip && pip install -e "."

COPY apps/automation-worker/ ./

# The base image ships a `pwuser` with the browser sandbox configured.
RUN mkdir -p /srv/storage/traces && chown -R pwuser:pwuser /srv
USER pwuser

CMD ["arq", "worker.main.WorkerSettings"]
