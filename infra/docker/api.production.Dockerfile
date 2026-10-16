# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/api
COPY apps/api/pyproject.toml ./
COPY apps/api/app ./app
RUN pip install --upgrade pip && pip install .

RUN groupadd --system --gid 10003 renderdata \
    && useradd --create-home --uid 10001 --gid renderdata appuser \
    && mkdir -p /srv/storage/renders \
    && chown -R appuser:renderdata /srv \
    && chmod 2775 /srv/storage/renders
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
