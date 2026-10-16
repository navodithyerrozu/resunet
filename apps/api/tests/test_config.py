from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Environment, Settings


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": Environment.PRODUCTION,
        "jwt_secret_key": "a" * 48,
        "cors_origins": ["https://resunet.example"],
        "mongodb_uri": "mongodb+srv://db.example/resunet",
        "redis_uri": "rediss://cache.example:6380/0",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_accepts_remote_dependencies_and_explicit_secrets() -> None:
    settings = _production_settings()

    assert settings.is_production


def test_resume_agent_configuration_only_exposes_supported_anthropic_provider() -> None:
    settings = Settings(_env_file=None)

    assert settings.anthropic_api_key is None
    assert not hasattr(settings, "openai_api_key")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mongodb_uri", "mongodb://localhost:27017/resunet"),
        ("mongodb_uri", "mongodb://127.0.0.1:27017/resunet"),
        ("redis_uri", "redis://localhost:6379/0"),
        ("redis_uri", "redis://[::1]:6379/0"),
    ],
)
def test_production_rejects_loopback_database_services(field: str, value: str) -> None:
    with pytest.raises(ValidationError, match="non-local service"):
        _production_settings(**{field: value})
