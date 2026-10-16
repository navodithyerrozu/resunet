from __future__ import annotations

from typing import Any

import pytest

from worker.browser import _restrict_navigation
from worker.settings import AutomationSettings


class FakeRequest:
    def __init__(
        self,
        url: str,
        *,
        navigation: bool = True,
        method: str = "GET",
        resource_type: str = "document",
    ) -> None:
        self.url = url
        self.navigation = navigation
        self.method = method
        self.resource_type = resource_type

    def is_navigation_request(self) -> bool:
        return self.navigation


class FakeRoute:
    def __init__(self, url: str, **request_options: Any) -> None:
        self.request = FakeRequest(url, **request_options)
        self.action: tuple[str, str | None] | None = None

    async def abort(self, error_code: str) -> None:
        self.action = ("abort", error_code)

    async def continue_(self) -> None:
        self.action = ("continue", None)


@pytest.fixture
def settings() -> AutomationSettings:
    return AutomationSettings(
        _env_file=None,
        environment="development",
        automation_allowed_hosts="fixture.local,localhost,127.0.0.1",
    )


@pytest.mark.asyncio
async def test_external_navigation_is_blocked(settings: AutomationSettings) -> None:
    route = FakeRoute("https://unapproved.example/apply")

    await _restrict_navigation(route, settings, "application-id")  # type: ignore[arg-type]

    assert route.action == ("abort", "blockedbyclient")


@pytest.mark.asyncio
async def test_allowlisted_navigation_continues(settings: AutomationSettings) -> None:
    route = FakeRoute("https://fixture.local/apply")

    await _restrict_navigation(route, settings, "application-id")  # type: ignore[arg-type]

    assert route.action == ("continue", None)


@pytest.mark.asyncio
async def test_external_subresource_is_blocked(
    settings: AutomationSettings,
) -> None:
    route = FakeRoute(
        "https://cdn.example/assets/app.js",
        navigation=False,
        resource_type="script",
    )

    await _restrict_navigation(route, settings, "application-id")  # type: ignore[arg-type]

    assert route.action == ("abort", "blockedbyclient")


@pytest.mark.asyncio
async def test_allowlisted_subresource_continues(settings: AutomationSettings) -> None:
    route = FakeRoute(
        "https://fixture.local/assets/app.js",
        navigation=False,
        resource_type="script",
    )

    await _restrict_navigation(route, settings, "application-id")  # type: ignore[arg-type]

    assert route.action == ("continue", None)


@pytest.mark.asyncio
async def test_external_data_request_is_blocked(settings: AutomationSettings) -> None:
    route = FakeRoute(
        "https://unapproved.example/submit",
        navigation=False,
        method="POST",
        resource_type="fetch",
    )

    await _restrict_navigation(route, settings, "application-id")  # type: ignore[arg-type]

    assert route.action == ("abort", "blockedbyclient")
