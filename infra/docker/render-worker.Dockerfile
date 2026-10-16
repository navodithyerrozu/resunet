# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# LaTeX render worker.
#
# texlive-full is ~4GB; the scheme-medium subset below plus the specific
# packages ATS-friendly resume templates actually need lands around 700MB.
# If you add a template that needs a new package, add it here explicitly
# rather than reaching for texlive-full.
# ---------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-latex-base \
        texlive-latex-recommended \
        texlive-latex-extra \
        texlive-fonts-recommended \
        lmodern \
        latexmk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/render-worker

COPY apps/render-worker/pyproject.toml ./
COPY apps/render-worker/worker ./worker
RUN pip install --upgrade pip && pip install .

RUN groupadd --system --gid 10003 renderdata \
    && useradd --create-home --uid 10002 --gid renderdata renderuser \
    && mkdir -p /srv/storage/renders \
    && chown -R renderuser:renderdata /srv \
    && chmod 2775 /srv/storage/renders
USER renderuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD ["arq", "worker.main.WorkerSettings", "--check"]

CMD ["arq", "worker.main.WorkerSettings"]
