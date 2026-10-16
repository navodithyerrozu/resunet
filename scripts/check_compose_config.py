"""Validate service secrets, automation host allowlist, and shared render paths."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION_ENV = {
    "MONGODB_URI": "mongodb+srv://mongo.validation.invalid/resunet",
    "REDIS_URI": "rediss://redis.validation.invalid:6380/0",
    "JWT_SECRET_KEY": "validation-only-secret-not-for-use-0123456789",
    "CORS_ORIGINS": "https://app.validation.invalid",
    "CREDENTIAL_ENCRYPTION_KEY": "validation-only-worker-key",
    "AUTOMATION_PROXY_URL": "http://proxy.validation.invalid:8080",
    "AUTOMATION_ALLOWED_HOSTS": "fixture.validation.invalid",
    "AUTOMATION_REQUIRE_APPROVAL": "true",
    "ANTHROPIC_API_KEY": "validation-only-provider-key",
    "ENVIRONMENT": "development",
}
PRIVATE_ENV_KEYS = {
    "JWT_SECRET_KEY",
    "CREDENTIAL_ENCRYPTION_KEY",
    "AUTOMATION_PROXY_URL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
}


def _compose_config(*compose_args: str) -> dict[str, Any]:
    env = os.environ.copy()
    env.update(VALIDATION_ENV)
    result = subprocess.run(
        ["docker", "compose", *compose_args, "config", "--format", "json"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=True,
        text=True,
    )
    return json.loads(result.stdout)


def _environment(config: dict[str, Any], service: str) -> dict[str, str]:
    return config["services"][service].get("environment", {})


def _assert_service_isolation(config: dict[str, Any], *, production: bool) -> None:
    api = _environment(config, "api")
    render = _environment(config, "render-worker")
    automation = _environment(config, "automation-worker")
    web = _environment(config, "web")

    assert api["RENDER_STORAGE_DIR"] == "/srv/storage/renders"
    assert render["RENDER_STORAGE_DIR"] == "/srv/storage/renders"
    assert automation["RENDER_STORAGE_DIR"] == "/srv/storage/renders"
    assert automation["TRACE_STORAGE_DIR"] == "/srv/storage/traces"
    for service in ("api", "render-worker", "automation-worker"):
        mounts = config["services"][service].get("volumes", [])
        assert any(mount.get("target") == "/srv/storage/renders" for mount in mounts)

    assert not (PRIVATE_ENV_KEYS & render.keys())
    assert not (PRIVATE_ENV_KEYS & web.keys())
    assert "AUTOMATION_ALLOWED_HOSTS" not in render
    assert "AUTOMATION_ALLOWED_HOSTS" not in web
    assert not ({"JWT_SECRET_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"} & automation.keys())
    assert automation["CREDENTIAL_ENCRYPTION_KEY"] == VALIDATION_ENV[
        "CREDENTIAL_ENCRYPTION_KEY"
    ]
    assert automation["AUTOMATION_PROXY_URL"] == VALIDATION_ENV["AUTOMATION_PROXY_URL"]
    assert automation["AUTOMATION_ALLOWED_HOSTS"] == VALIDATION_ENV["AUTOMATION_ALLOWED_HOSTS"]
    assert automation["AUTOMATION_REQUIRE_APPROVAL"].lower() == "true"
    assert "AUTOMATION_ALLOWED_HOSTS" not in api

    assert api["CREDENTIAL_ENCRYPTION_KEY"] == ""
    assert api["JWT_SECRET_KEY"] == VALIDATION_ENV["JWT_SECRET_KEY"]
    assert api["ANTHROPIC_API_KEY"] == VALIDATION_ENV["ANTHROPIC_API_KEY"]
    assert "OPENAI_API_KEY" not in api
    if production:
        assert "AUTOMATION_PROXY_URL" not in api
    else:
        assert api["AUTOMATION_PROXY_URL"] == ""


def main() -> None:
    development = _compose_config()
    production = _compose_config("-f", "docker-compose.production.yml")
    _assert_service_isolation(development, production=False)
    _assert_service_isolation(production, production=True)
    print("Compose secret isolation, automation allowlist, and shared storage checks passed.")


if __name__ == "__main__":
    main()
