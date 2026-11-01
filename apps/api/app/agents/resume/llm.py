"""LLM client construction for the agent.

Isolated in one module so model choice, timeouts, and retry behaviour are
configured once. Nodes ask for a model by role, not by name, which means
swapping the drafting model (or pointing it at a cheaper one) is a change
here rather than across six node modules.

Two providers are supported, selected by LLM_PROVIDER:

* ``anthropic`` (default) -- ChatAnthropic, requires ANTHROPIC_API_KEY.
* ``ollama`` -- a local model served by Ollama, no API key. The model must
  support structured output (llama3.1:8b, the default, does); ChatOllama
  defaults to the ``json_schema`` structured-output method, which sends the
  schema in Ollama's ``format`` field as constrained generation, so the
  nodes' ``structured(...)`` calls work unchanged for both providers.
  Restart the API after changing LLM_PROVIDER -- the client is cached.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_core.language_models import BaseChatModel

from app.core.config import get_settings
from app.core.errors import ExternalServiceError


@lru_cache(maxsize=4)
def get_chat_model(*, temperature: float | None = None) -> BaseChatModel:
    """Build (and cache) a chat model for the configured provider.

    Cached because the client holds a connection pool; constructing one per
    node call would open a new pool on every turn.

    Temperature is low by default. Resume bullets are a precision task -- the
    facts come from the user's notes, and creative variation on someone's
    employment history is a bug, not a feature.
    """
    settings = get_settings()
    resolved_temperature = (
        settings.resume_agent_temperature if temperature is None else temperature
    )
    if settings.llm_provider == "ollama":
        try:
            from langchain_ollama import ChatOllama
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ExternalServiceError(
                "LLM_PROVIDER=ollama but langchain-ollama is not installed; "
                "install it (pip install langchain-ollama) and restart the API."
            ) from exc
        # Local models pay no per-token cost, so the timeout is about the
        # first load into memory, not spend: never let it be shorter than
        # 180s, or a cold model load looks like a failure.
        return ChatOllama(
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            temperature=resolved_temperature,
            timeout=max(settings.llm_timeout_seconds, 180),
        )
    if not settings.anthropic_api_key:
        raise ExternalServiceError(
            "ANTHROPIC_API_KEY is not configured; the resume agent cannot run. "
            "Set it, or switch to a local model with LLM_PROVIDER=ollama."
        )
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model_name=settings.resume_agent_model,
        temperature=resolved_temperature,
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
        stop=None,
    )


def structured(schema: type, *, temperature: float | None = None) -> Any:
    """A model bound to a Pydantic output schema.

    Every node that produces content uses this rather than parsing free text.
    Structured output is what makes the XYZ components separable in the first
    place -- see app/models/bullet.py for why that separation is the whole
    design. The underlying chat model decides the mechanism (Anthropic tool
    calling, Ollama json_schema constrained generation); the nodes do not.
    """
    return get_chat_model(temperature=temperature).with_structured_output(schema)
