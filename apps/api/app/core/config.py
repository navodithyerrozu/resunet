"""Application settings, loaded from the environment via pydantic-settings.

Settings are validated once at import and cached. Fail-fast on a malformed
config is deliberate: a missing JWT secret should crash the process at boot,
not surface as an authentication bug in production.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Walks up to the repo root so the API picks up the same .env the
        # workers and Compose use -- one file, no drift.
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Core -------------------------------------------------------------
    environment: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"
    api_base_url: str = "http://localhost:8000"
    project_name: str = "ResuNet API"
    api_v1_prefix: str = "/api/v1"

    # --- Mongo ------------------------------------------------------------
    mongodb_uri: str = "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true"
    mongodb_db: str = "resunet"
    mongo_max_pool_size: int = Field(default=50, ge=1, le=500)
    mongo_min_pool_size: int = Field(default=0, ge=0)

    # --- Redis / queue ----------------------------------------------------
    redis_uri: str = "redis://localhost:6379/0"
    render_queue_name: str = "resunet:render"
    automation_queue_name: str = "resunet:automation"

    # --- Auth -------------------------------------------------------------
    jwt_secret_key: str = Field(
        default="change-me-openssl-rand-hex-32",
        min_length=16,
        description="HMAC signing key. Rotating it invalidates all outstanding tokens.",
    )
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=365)

    # --- CORS -------------------------------------------------------------
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        description="Explicit allowlist. Never '*' with credentials enabled -- browsers "
        "reject that combination and it would be unsafe if they did not.",
    )

    # --- LLM --------------------------------------------------------------
    anthropic_api_key: str | None = None
    resume_agent_model: str = "claude-sonnet-4-5-20250929"
    resume_agent_temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    xyz_max_repair_attempts: int = Field(
        default=3, ge=1, le=6,
        description="Repair passes before a bullet is flagged for human review. Bounds "
        "worst-case cost and latency for a single bullet.",
    )
    llm_timeout_seconds: int = Field(default=60, ge=5, le=300)

    # --- Rendering --------------------------------------------------------
    render_storage_dir: Path = Path("./storage/renders")
    latex_compile_timeout_seconds: int = Field(default=45, ge=5, le=300)

    # --- Automation -------------------------------------------------------
    automation_max_attempts: int = Field(default=3, ge=1, le=10)
    automation_require_approval: bool = Field(
        default=True,
        description="Pause before the final submit click and wait for a human. Default "
        "on: an application is an irreversible write to someone else's system.",
    )

    # --- Credentials ------------------------------------------------------
    credential_encryption_key: str | None = Field(
        default=None,
        description="Fernet key. Intentionally unset in the API process -- only the "
        "automation worker receives it, so an API compromise cannot decrypt "
        "job-board logins.",
    )

    # --- Rate limiting ----------------------------------------------------
    rate_limit_per_minute: int = Field(default=120, ge=1)
    agent_rate_limit_per_hour: int = Field(
        default=60, ge=1, description="LLM turns per user per hour. Caps runaway spend."
    )
    auth_rate_limit_per_minute: int = Field(
        default=10, ge=1, description="Registration and login attempts per email per minute."
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accept a comma-separated string, since env vars cannot hold lists."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        """Refuse to boot production with development defaults.

        This check has saved more incidents than any amount of documentation.
        """
        if self.environment is Environment.PRODUCTION:
            if self.jwt_secret_key == "change-me-openssl-rand-hex-32":  # noqa: S105
                raise ValueError("JWT_SECRET_KEY must be set in production")
            if any("localhost" in o for o in self.cors_origins):
                raise ValueError("CORS origins must not include localhost in production")
            for label, uri in (
                ("MONGODB_URI", self.mongodb_uri),
                ("REDIS_URI", self.redis_uri),
            ):
                hostname = urlparse(uri).hostname
                is_loopback = False
                if hostname:
                    try:
                        is_loopback = ip_address(hostname).is_loopback
                    except ValueError:
                        is_loopback = hostname.casefold() == "localhost"
                if not hostname or is_loopback:
                    raise ValueError(f"{label} must point to a non-local service in production")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def docs_url(self) -> str | None:
        """Hide interactive docs in production; they leak the full schema."""
        return None if self.is_production else "/docs"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor. Also the FastAPI dependency."""
    return Settings()
