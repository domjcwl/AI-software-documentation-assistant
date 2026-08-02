"""No real OpenAI calls here. The retry-then-succeed and retry-exhausted
tests patch tenacity's sleep so they run instantly instead of actually
backing off for real seconds."""

from __future__ import annotations

import time

import httpx
import openai
import pytest

from app.config import Settings
from app.errors import OpenAIAuthError, OpenAIRateLimitedError
from app.vectorstore.embedder import Embedder


def _settings(**overrides) -> Settings:
    overrides.setdefault("openai_api_key", "sk-test")
    return Settings(_env_file=None, **overrides)


class _FakeItem:
    def __init__(self, vector):
        self.embedding = vector


class _FakeResponse:
    def __init__(self, vectors):
        self.data = [_FakeItem(v) for v in vectors]


def _status_error(cls, status_code, message="error"):
    request = httpx.Request("POST", "https://api.openai.com/v1/embeddings")
    response = httpx.Response(status_code, request=request, json={"error": {"message": message}})
    return cls(message, response=response, body=None)


def test_embedder_requires_api_key():
    with pytest.raises(OpenAIAuthError):
        Embedder(_settings(openai_api_key=None))


def test_embed_texts_batches_and_reports_progress(monkeypatch):
    embedder = Embedder(_settings())
    calls: list[int] = []

    def fake_create(*, model, input):
        calls.append(len(input))
        return _FakeResponse([[float(len(t))] for t in input])

    monkeypatch.setattr(embedder._client.embeddings, "create", fake_create)

    texts = [f"text-{i}" for i in range(150)]  # spans two 96-sized batches
    progress: list[tuple[int, int]] = []
    vectors = embedder.embed_texts(texts, on_batch=lambda done, total: progress.append((done, total)))

    assert len(vectors) == 150
    assert calls == [96, 54]
    assert progress == [(96, 150), (150, 150)]


def test_embed_texts_auth_error_is_not_retried(monkeypatch):
    embedder = Embedder(_settings())
    call_count = 0

    def fake_create(*, model, input):
        nonlocal call_count
        call_count += 1
        raise _status_error(openai.AuthenticationError, 401)

    monkeypatch.setattr(embedder._client.embeddings, "create", fake_create)

    with pytest.raises(OpenAIAuthError):
        embedder.embed_texts(["a"])
    assert call_count == 1, "a bad API key should fail fast, not burn through retries"


def test_embed_texts_rate_limit_retries_then_succeeds(monkeypatch):
    embedder = Embedder(_settings())
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    call_count = 0

    def fake_create(*, model, input):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _status_error(openai.RateLimitError, 429)
        return _FakeResponse([[1.0] for _ in input])

    monkeypatch.setattr(embedder._client.embeddings, "create", fake_create)

    vectors = embedder.embed_texts(["a"])
    assert vectors == [[1.0]]
    assert call_count == 2


def test_embed_texts_rate_limit_exhausts_retries(monkeypatch):
    embedder = Embedder(_settings())
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    call_count = 0

    def fake_create(*, model, input):
        nonlocal call_count
        call_count += 1
        raise _status_error(openai.RateLimitError, 429)

    monkeypatch.setattr(embedder._client.embeddings, "create", fake_create)

    with pytest.raises(OpenAIRateLimitedError):
        embedder.embed_texts(["a"])
    assert call_count == 5  # stop_after_attempt(5) in app.vectorstore.embedder
