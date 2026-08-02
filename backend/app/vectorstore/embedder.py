"""Batched OpenAI embeddings with retry. See planning/architecture.md §3.5.

API surface (client construction, embeddings.create, exception classes)
verified against the installed openai==2.52.0 rather than assumed —
SDK majors have moved fast enough that older examples are unreliable.
"""

from __future__ import annotations

from collections.abc import Callable

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import Settings
from app.errors import InternalError, OpenAIAuthError, OpenAIRateLimitedError

_BATCH_SIZE = 96


class Embedder:
    """One instance per indexing job; not shared across threads."""

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise OpenAIAuthError("OPENAI_API_KEY is not set.")
        self._client = OpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_embed_model

    def embed_texts(
        self, texts: list[str], on_batch: Callable[[int, int], None] | None = None
    ) -> list[list[float]]:
        """Embed all texts, batched at 96 per request, preserving order.

        on_batch(completed, total), if given, fires after each batch so a
        caller can report progress — this is purely informational; the
        vectors themselves are only returned once every batch succeeds,
        so a caller that inserts them into storage only after this
        returns can never end up with a half-embedded project.
        """
        vectors: list[list[float]] = []
        total = len(texts)
        for start in range(0, total, _BATCH_SIZE):
            batch = texts[start : start + _BATCH_SIZE]
            vectors.extend(self._embed_batch_with_retry(batch))
            if on_batch is not None:
                on_batch(len(vectors), total)
        return vectors

    def _embed_batch_with_retry(self, batch: list[str]) -> list[list[float]]:
        try:
            return self._embed_batch(batch)
        except AuthenticationError as exc:
            raise OpenAIAuthError(f"OpenAI rejected the API key: {exc}") from exc
        except RateLimitError as exc:
            raise OpenAIRateLimitedError(f"OpenAI rate limit exceeded after retries: {exc}") from exc
        except (APIConnectionError, APIStatusError) as exc:
            raise InternalError(f"OpenAI embeddings request failed: {exc}") from exc

    @retry(
        retry=retry_if_exception_type((RateLimitError, APIConnectionError, InternalServerError)),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(model=self._model, input=batch)
        return [item.embedding for item in response.data]
