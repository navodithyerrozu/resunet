from __future__ import annotations

import pytest
from pydantic import ValidationError

from worker.settings import AutomationSettings


def _production_settings(**overrides: object) -> AutomationSettings:
    values: dict[str, object] = {
        "environment": "production",
        "automation_allowed_hosts": "boards.example.com",
        "automation_proxy_url": "http://proxy.example:8080",
        "automation_require_approval": True,
    }
    values.update(overrides)
    return AutomationSettings(_env_file=None, **values)


def test_production_settings_accept_explicit_host_and_human_approval() -> None:
    settings = _production_settings()

    assert settings.is_production
    assert settings.automation_require_approval
    assert settings.is_application_url_authorized("https://boards.example.com/job/123")


def test_production_does_not_require_a_proxy() -> None:
    settings = _production_settings(automation_proxy_url=None)

    assert settings.automation_proxy_url is None


def test_production_requires_explicit_allowed_hosts() -> None:
    with pytest.raises(ValidationError, match="AUTOMATION_ALLOWED_HOSTS is required"):
        _production_settings(automation_allowed_hosts="")


def test_host_allowlist_is_exact_and_production_requires_https() -> None:
    settings = _production_settings(automation_allowed_hosts="boards.example.com")

    assert not settings.is_application_url_authorized("https://sub.boards.example.com/job")
    assert not settings.is_application_url_authorized("http://boards.example.com/job")
    assert not settings.is_application_url_authorized("https://boards.example.net/job")
    assert not settings.is_application_url_authorized(
        "https://boards.example.com@attacker.example/job"
    )


def test_host_allowlist_rejects_wildcards_and_url_paths() -> None:
    for invalid in ("*.example.com", "https://boards.example.com/jobs"):
        with pytest.raises(ValidationError, match="exact hostnames or IP addresses"):
            _production_settings(automation_allowed_hosts=invalid)


def test_production_requires_human_approval() -> None:
    with pytest.raises(ValidationError, match="cannot be disabled in production"):
        _production_settings(automation_require_approval=False)
