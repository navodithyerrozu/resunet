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
RUN pip install --upgrade pip && pip install -e "."

COPY apps/render-worker/ ./

RUN useradd --create-home --uid 10002 renderuser \
    && mkdir -p /srv/storage/renders \
    && chown -R renderuser:renderuser /srv
USER renderuser

CMD ["arq", "worker.main.WorkerSettings"]
