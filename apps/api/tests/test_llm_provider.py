"""Tests for the LLM provider switch in app/agents/resume/llm.py.

The provider is chosen by Settings.llm_provider, defaulting to Anthropic so
existing behaviour is unchanged unless the user opts into a local model.
Ollama is faked here: constructing a real ChatOllama needs no server, but
importing langchain_ollama is environment-sensitive, and these tests only
need to prove the switch picks the right client with the right settings.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from app.agents.resume import llm
from app.core.config import Settings
from app.core.errors import ExternalServiceError


@pytest.fixture(autouse=True)
def _clear_model_cache():
    llm.get_chat_model.cache_clear()
    yield
    llm.get_chat_model.cache_clear()


def _settings(**overrides: object) -> SimpleNamespace:
    base = {
        "llm_provider": "anthropic",
        "anthropic_api_key": None,
        "resume_agent_model": "claude-sonnet-4-5-20250929",
        "resume_agent_temperature": 0.2,
        "llm_timeout_seconds": 60,
        "ollama_base_url": "http://localhost:11434",
        "ollama_model": "llama3.1:8b",
        "xyz_max_repair_attempts": 3,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_defaults_keep_anthropic() -> None:
    assert Settings.model_fields["llm_provider"].default == "anthropic"
    assert Settings.model_fields["ollama_model"].default == "llama3.1:8b"
    assert Settings.model_fields["ollama_base_url"].default == "http://localhost:11434"


def test_anthropic_without_key_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "get_settings", lambda: _settings())
    with pytest.raises(ExternalServiceError, match="ANTHROPIC_API_KEY"):
        llm.get_chat_model()


def test_ollama_needs_no_key_and_uses_local_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeChatOllama:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def with_structured_output(self, schema: type) -> str:
            return f"structured:{schema.__name__}"

    fake_module = types.ModuleType("langchain_ollama")
    fake_module.ChatOllama = FakeChatOllama  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "langchain_ollama", fake_module)
    monkeypatch.setattr(
        llm, "get_settings", lambda: _settings(llm_provider="ollama")
    )

    model = llm.get_chat_model()
    assert captured["model"] == "llama3.1:8b"
    assert captured["base_url"] == "http://localhost:11434"
    assert captured["temperature"] == 0.2
    # Cold model load, not token spend: timeout must not stay at 60s.
    assert captured["timeout"] == 180
    assert llm.structured(dict) == "structured:dict"  # type: ignore[arg-type]
    assert isinstance(model, FakeChatOllama)


def test_ollama_missing_package_fails_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "langchain_ollama", raising=False)
    monkeypatch.setattr(llm, "get_settings", lambda: _settings(llm_provider="ollama"))
    # Block the real package from being importable in this test.
    import builtins

    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if name == "langchain_ollama":
            raise ImportError("no module named langchain_ollama")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    with pytest.raises(ExternalServiceError, match="langchain-ollama"):
        llm.get_chat_model()
