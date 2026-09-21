"""Render-worker configuration.

A deliberately narrow subset of the API's settings. The worker reads the same
`.env`, so shared keys keep the same names, but it declares only what it uses:
a worker that cannot see `JWT_SECRET_KEY` or `CREDENTIAL_ENCRYPTION_KEY` cannot
leak them.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RenderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    environment: str = "development"
    log_level: str = "INFO"

    mongodb_uri: str = "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true"
    mongodb_db: str = "resunet"

    redis_uri: str = "redis://localhost:6379/0"
    render_queue_name: str = "resunet:render"

    render_storage_dir: Path = Path("./storage/renders")

    latex_compile_timeout_seconds: int = Field(
        default=45,
        ge=5,
        le=300,
        description="Hard wall-clock ceiling on pdflatex. A malformed macro can spin "
        "forever; TeX has no internal timeout, so this subprocess kill is the only "
        "thing standing between one bad resume and a permanently wedged worker.",
    )
    latex_passes: int = Field(
        default=2,
        ge=1,
        le=3,
        description="latexmk resolves cross-references itself, but page-count "
        "totals in the footer need the second pass to stabilise.",
    )

    render_max_pages: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Renders exceeding this are still stored but flagged. A four-page "
        "resume is a content problem the user should see, not a render failure.",
    )
    render_concurrency: int = Field(
        default=2,
        ge=1,
        le=16,
        description="Concurrent compiles per worker process. pdflatex is CPU-bound and "
        "single-threaded, so useful concurrency tracks core count, not IO capacity.",
    )
    render_job_timeout_seconds: int = Field(default=120, ge=10, le=600)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> RenderSettings:
    return RenderSettings()
