"""End-to-end pipeline test with the network boundary (github.fetch_repo)
and the paid boundary (Embedder) both replaced by fakes — everything in
between (scan, chunk, manifest, Chroma storage, project-record progress)
is real. See test_github.py for fetch_repo's own mocked-network tests and
test_embedder.py for Embedder's own tests.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import Settings
from app.errors import RepoNotFoundError
from app.ingestion.pipeline import run_indexing
from app.services.project_store import ProjectStore
from app.vectorstore.store import VectorStore

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mini_repo"


class _FakeEmbedder:
    """Deterministic, zero-cost stand-in for app.vectorstore.embedder.Embedder."""

    def __init__(self, settings):
        pass

    def embed_texts(self, texts, on_batch=None):
        if on_batch:
            on_batch(len(texts), len(texts))
        return [[float(i % 7), 0.1, 0.2] for i in range(len(texts))]


def _fake_fetch_repo(owner, repo, ref, dest_dir, settings):
    shutil.copytree(FIXTURE_DIR, dest_dir)
    return ref or "main"


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        openai_api_key="sk-test",
        repo_dir=tmp_path / "repos",
        chroma_dir=tmp_path / "chroma",
    )


@pytest.fixture(autouse=True)
def _patch_settings(monkeypatch, tmp_path):
    """run_indexing calls get_settings() itself (it's the BackgroundTasks
    entry point and takes no settings argument), so this is patched at
    the module level rather than passed in."""
    settings = _settings(tmp_path)
    monkeypatch.setattr("app.ingestion.pipeline.get_settings", lambda: settings)


def test_run_indexing_success(monkeypatch, tmp_path):
    monkeypatch.setattr("app.ingestion.github.fetch_repo", _fake_fetch_repo)
    monkeypatch.setattr("app.ingestion.pipeline.Embedder", _FakeEmbedder)

    store = ProjectStore(persist_path=None)
    vectors = VectorStore(_settings(tmp_path))
    record, _ = store.create(name="octocat/hello", repo_url="https://github.com/octocat/hello", ref=None)

    run_indexing(record.project_id, store=store, vectors=vectors)

    updated = store.get(record.project_id)
    assert updated.status == "ready"
    assert updated.percent == 100
    assert updated.files_indexed > 0
    assert updated.chunks_embedded > 0
    assert updated.error is None

    # everything that was embedded actually made it into Chroma
    assert vectors.count(record.project_id) == updated.chunks_embedded

    manifest_path = _settings(tmp_path).repo_dir / f"{record.project_id}.manifest.json"
    assert manifest_path.exists()


def test_run_indexing_excludes_pruned_and_binary_content(monkeypatch, tmp_path):
    """The fixture repo contains node_modules/, .git/, and a lockfile —
    confirms the real scanner rules ran inside the full pipeline, not
    just in isolation in test_scanner.py."""
    monkeypatch.setattr("app.ingestion.github.fetch_repo", _fake_fetch_repo)
    monkeypatch.setattr("app.ingestion.pipeline.Embedder", _FakeEmbedder)

    store = ProjectStore(persist_path=None)
    vectors = VectorStore(_settings(tmp_path))
    record, _ = store.create(name="octocat/hello", repo_url="https://github.com/octocat/hello", ref=None)

    run_indexing(record.project_id, store=store, vectors=vectors)

    updated = store.get(record.project_id)
    # main.py, src/utils.py, README.md == 3 real files; node_modules/.git/
    # lockfile are excluded
    assert updated.files_indexed == 3


def test_run_indexing_failure_marks_failed_and_cleans_up(monkeypatch, tmp_path):
    def _raise_not_found(owner, repo, ref, dest_dir, settings):
        raise RepoNotFoundError("nope")

    monkeypatch.setattr("app.ingestion.github.fetch_repo", _raise_not_found)

    store = ProjectStore(persist_path=None)
    vectors = VectorStore(_settings(tmp_path))
    record, _ = store.create(name="octocat/missing", repo_url="https://github.com/octocat/missing", ref=None)

    run_indexing(record.project_id, store=store, vectors=vectors)

    updated = store.get(record.project_id)
    assert updated.status == "failed"
    assert updated.error is not None
    assert updated.error.code == "repo_not_found"

    assert vectors.count(record.project_id) == 0
    assert not (_settings(tmp_path).repo_dir / record.project_id).exists()


def test_run_indexing_missing_openai_key_fails_after_chunking(monkeypatch, tmp_path):
    """No Embedder mock here — proves the real auth check fires, and that
    fetch/scan/chunk all completed first (files_indexed is populated)
    before the failure, since embedding is the last stage."""
    monkeypatch.setattr("app.ingestion.github.fetch_repo", _fake_fetch_repo)

    settings = Settings(
        _env_file=None,
        openai_api_key=None,
        repo_dir=tmp_path / "repos",
        chroma_dir=tmp_path / "chroma",
    )
    monkeypatch.setattr("app.ingestion.pipeline.get_settings", lambda: settings)

    store = ProjectStore(persist_path=None)
    vectors = VectorStore(settings)
    record, _ = store.create(name="octocat/hello", repo_url="https://github.com/octocat/hello", ref=None)

    run_indexing(record.project_id, store=store, vectors=vectors)

    updated = store.get(record.project_id)
    assert updated.status == "failed"
    assert updated.error.code == "openai_auth"
    assert updated.files_indexed == 3  # fetch/scan/chunk succeeded before the embedding stage failed


def test_run_indexing_unknown_project_id_is_a_noop(tmp_path):
    store = ProjectStore(persist_path=None)
    vectors = VectorStore(_settings(tmp_path))
    run_indexing("does-not-exist", store=store, vectors=vectors)  # must not raise
