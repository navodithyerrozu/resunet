"""LLM client construction for the agent.

Isolated in one module so model choice, timeouts, and retry behaviour are
configured once. Nodes ask for a model by role, not by name, which means
swapping the drafting model (or pointing it at a cheaper one) is a change
here rather than across six node modules.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_anthropic import ChatAnthropic

from app.core.config import get_settings
from app.core.errors import ExternalServiceError


@lru_cache(maxsize=4)
def get_chat_model(*, temperature: float | None = None) -> ChatAnthropic:
    """Build (and cache) a chat model.

    Cached because the client holds a connection pool; constructing one per
    node call would open a new pool on every turn.

    Temperature is low by default. Resume bullets are a precision task -- the
    facts come from the user's notes, and creative variation on someone's
    employment history is a bug, not a feature.
    """
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ExternalServiceError(
            "ANTHROPIC_API_KEY is not configured; the resume agent cannot run."
        )
    return ChatAnthropic(
        model=settings.resume_agent_model,
        temperature=(
            settings.resume_agent_temperature if temperature is None else temperature
        ),
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
        stop=None,
    )


def structured(schema: type, *, temperature: float | None = None) -> Any:
    """A model bound to a Pydantic output schema.

    Every node that produces content uses this rather than parsing free text.
    Structured output is what makes the XYZ components separable in the first
    place -- see app/models/bullet.py for why that separation is the whole
    design.
    """
    return get_chat_model(temperature=temperature).with_structured_output(schema)
