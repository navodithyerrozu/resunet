"""Automation-worker configuration.

This is the only process in the system that receives
`CREDENTIAL_ENCRYPTION_KEY`. `docker-compose.yml` sets it to the empty string
for the API service specifically so that an API compromise cannot decrypt
job-board logins. If you add a service that reads this key, that isolation is
gone -- treat it as a design change, not a config change.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AutomationSettings(BaseSettings):
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
    automation_queue_name: str = "resunet:automation"

    render_storage_dir: Path = Path("./storage/renders")
    trace_storage_dir: Path = Path("./storage/traces")

    # --- Credentials ------------------------------------------------------
    credential_encryption_key: str | None = Field(
        default=None,
        description="Fernet key. Without it the worker can still submit to boards that "
        "need no login; credentialed boards fail closed with a clear error rather than "
        "attempting an anonymous submission.",
    )

    # --- Human-in-the-loop ------------------------------------------------
    automation_require_approval: bool = Field(
        default=True,
        description="Pause with the form filled and wait for a human to confirm the "
        "final click. Defaults on because submitting an application is an irreversible "
        "write to a third party's system that cannot be undone by us.",
    )

    # --- Retry / backoff --------------------------------------------------
    automation_max_attempts: int = Field(default=3, ge=1, le=10)
    automation_backoff_base_seconds: float = Field(default=30.0, ge=1.0)
    automation_backoff_max_seconds: float = Field(default=1800.0, ge=10.0)

    # --- Rate limiting ----------------------------------------------------
    automation_min_seconds_between_board_hits: float = Field(
        default=45.0,
        ge=0.0,
        description="Per-board floor between submissions across ALL users. Twenty "
        "applications to one ATS in ten seconds is the clearest bot signal there is, "
        "and the block lands on the shared egress IP -- so this limit protects every "
        "user on the platform, not just the one submitting.",
    )
    automation_board_failure_threshold: int = Field(
        default=5,
        ge=1,
        description="Consecutive failures before a board's circuit opens. Prevents the "
        "worker from grinding through a queue against a site that has started "
        "challenging every request.",
    )
    automation_circuit_cooldown_seconds: float = Field(default=1800.0, ge=60.0)

    # --- Browser ----------------------------------------------------------
    automation_headless: bool = Field(
        default=True,
        description="Headless is more detectable even with --headless=new: font "
        "metrics, GPU strings and a missing window manager all differ. Prefer "
        "headful behind a virtual display for boards that fingerprint aggressively.",
    )
    automation_browser_channel: str | None = Field(
        default=None,
        description="Set to 'chrome' to drive a real Chrome install rather than "
        "Playwright's bundled Chromium, whose build strings are a known tell.",
    )
    automation_proxy_url: str | None = Field(
        default=None,
        description="Residential or ISP proxy. Datacenter IP ranges are the single "
        "largest anti-bot trigger; cloud egress is often blocked before any "
        "behavioural signal is even evaluated.",
    )
    automation_locale: str = "en-US"
    automation_timezone: str = "America/New_York"
    automation_viewport_width: int = Field(default=1440, ge=800)
    automation_viewport_height: int = Field(default=900, ge=600)

    automation_nav_timeout_ms: int = Field(default=45_000, ge=5_000)
    automation_action_timeout_ms: int = Field(default=15_000, ge=1_000)
    automation_job_timeout_seconds: int = Field(default=600, ge=60)
    automation_concurrency: int = Field(
        default=1,
        ge=1,
        le=8,
        description="Concurrent browsers per worker. Kept at 1 by default: each "
        "Chromium is 300-500MB, and parallel submissions from one IP defeat the "
        "rate limiting above.",
    )

    # --- Evidence capture -------------------------------------------------
    automation_capture_trace: bool = Field(
        default=True,
        description="Playwright traces on failure. See worker/browser.py for why "
        "tracing must not be active during credential entry.",
    )
    automation_trace_retention_days: int = Field(
        default=14,
        ge=1,
        description="Traces and screenshots contain the full contents of a submitted "
        "application, which is sensitive personal data. Retention is bounded.",
    )

    @model_validator(mode="after")
    def _guard_production(self) -> AutomationSettings:
        """Refuse production configurations that are known to get IPs banned."""
        if self.environment == "production":
            if not self.automation_proxy_url:
                raise ValueError(
                    "AUTOMATION_PROXY_URL is required in production: submitting from "
                    "datacenter IPs gets the egress range blocked for every user."
                )
            if not self.automation_require_approval:
                raise ValueError(
                    "AUTOMATION_REQUIRE_APPROVAL cannot be disabled in production "
                    "without an explicit, documented product decision -- unattended "
                    "submission is irreversible and unrecoverable when it misfires."
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> AutomationSettings:
    return AutomationSettings()
