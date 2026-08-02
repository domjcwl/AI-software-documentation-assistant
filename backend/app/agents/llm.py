"""Chat-model construction, in one place.

Two tiers per planning/decisions.md ADR-010: the fast model does the
mechanical structured-output work (Coordinator, Review), the main model
writes the prose the user actually reads (Explanation). Both are
env-configured, so collapsing them to a single model is a .env change.
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.config import Settings
from app.errors import OpenAIAuthError


def _require_key(settings: Settings) -> str:
    if not settings.openai_api_key:
        raise OpenAIAuthError("OPENAI_API_KEY is not set.")
    return settings.openai_api_key


def fast_model(settings: Settings) -> ChatOpenAI:
    """For routing and review: deterministic, structured, cheap."""
    return ChatOpenAI(
        model=settings.openai_fast_model,
        api_key=_require_key(settings),
        temperature=0,
        timeout=60,
        max_retries=3,
    )


def chat_model(settings: Settings) -> ChatOpenAI:
    """For the user-visible explanation. Streams."""
    return ChatOpenAI(
        model=settings.openai_chat_model,
        api_key=_require_key(settings),
        temperature=0.1,
        timeout=120,
        max_retries=3,
        streaming=True,
    )
