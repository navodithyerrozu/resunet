"""Automation-worker configuration.

This is the only process in the system that receives
`CREDENTIAL_ENCRYPTION_KEY`. `docker-compose.yml` sets it to the empty string
for the API service specifically so that an API compromise cannot decrypt
job-board logins. If you add a service that reads this key, that isolation is
gone -- treat it as a design change, not a config change.
"""

from __future__ import annotations

import ipaddress
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

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
    automation_allowed_hosts: str = ""

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
        description="Run Chromium without a visible window. The worker does not "
        "modify browser fingerprints or attempt to evade access controls.",
    )
    automation_browser_channel: str | None = Field(
        default=None,
        description="Optional installed browser channel for compatibility testing.",
    )
    automation_proxy_url: str | None = Field(
        default=None,
        description="Optional organization-approved egress proxy. A proxy is not "
        "used to evade site restrictions; detected blocks stop the workflow.",
    )

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
        """Require a human gate and explicit host authorization in production."""
        for host in self.authorized_application_hosts:
            try:
                ipaddress.ip_address(host)
                continue
            except ValueError:
                pass
            labels = host.split(".")
            if not host or len(host) > 253 or any(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                for label in labels
            ):
                raise ValueError(
                    "AUTOMATION_ALLOWED_HOSTS must contain exact hostnames or IP addresses; "
                    "wildcards, URLs, and paths are not allowed."
                )
        if self.environment == "production":
            if not self.authorized_application_hosts:
                raise ValueError(
                    "AUTOMATION_ALLOWED_HOSTS is required in production and must list "
                    "each explicitly authorized application workflow hostname."
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

    @property
    def authorized_application_hosts(self) -> frozenset[str]:
        return frozenset(
            host.strip().lower().rstrip(".")
            for host in self.automation_allowed_hosts.split(",")
            if host.strip()
        )

    def is_application_url_authorized(self, url: str) -> bool:
        """Match an exact allowlisted hostname and reject insecure remote URLs."""
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower().rstrip(".")
        except ValueError:
            return False
        if not host or parsed.username or parsed.password:
            return False
        if host not in self.authorized_application_hosts:
            return False
        if parsed.scheme == "https":
            return True
        return (
            not self.is_production
            and parsed.scheme == "http"
            and host in {"localhost", "127.0.0.1", "::1"}
        )


@lru_cache(maxsize=1)
def get_settings() -> AutomationSettings:
    return AutomationSettings()
