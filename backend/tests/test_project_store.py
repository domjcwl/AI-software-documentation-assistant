"""Persistence is tested directly against a standalone ProjectStore
here, not the process-wide singleton — see backend/conftest.py for why
API-level tests disable persistence on the shared instance entirely."""

from __future__ import annotations

from app.services.project_store import ProjectStore


def test_create_persists_and_reloads_across_instances(tmp_path):
    path = tmp_path / "projects.json"
    store = ProjectStore(persist_path=path)
    record, created = store.create(name="a/b", repo_url="https://github.com/a/b", ref=None)
    assert created is True
    assert path.exists()

    reloaded = ProjectStore(persist_path=path)
    fetched = reloaded.get(record.project_id)
    assert fetched.name == "a/b"
    assert fetched.repo_url == "https://github.com/a/b"
    assert fetched.status == "queued"


def test_update_persists_across_instances(tmp_path):
    path = tmp_path / "projects.json"
    store = ProjectStore(persist_path=path)
    record, _ = store.create(name="a/b", repo_url="https://github.com/a/b", ref=None)
    store.update(record.project_id, status="ready", percent=100, chunks_embedded=42)

    reloaded = ProjectStore(persist_path=path)
    fetched = reloaded.get(record.project_id)
    assert fetched.status == "ready"
    assert fetched.percent == 100
    assert fetched.chunks_embedded == 42


def test_delete_persists_across_instances(tmp_path):
    path = tmp_path / "projects.json"
    store = ProjectStore(persist_path=path)
    record, _ = store.create(name="a/b", repo_url="https://github.com/a/b", ref=None)
    store.delete(record.project_id)

    reloaded = ProjectStore(persist_path=path)
    assert reloaded.list() == []


def test_missing_persist_file_starts_empty(tmp_path):
    store = ProjectStore(persist_path=tmp_path / "does-not-exist.json")
    assert store.list() == []


def test_corrupt_persist_file_starts_empty_without_raising(tmp_path):
    path = tmp_path / "projects.json"
    path.write_text("not valid json", encoding="utf-8")
    store = ProjectStore(persist_path=path)
    assert store.list() == []


def test_create_second_time_after_reload_dedupes_correctly(tmp_path):
    """Regression guard for the ref-mutation dedupe bug caught during
    development: create() must still find the existing record for the
    same (repo_url, ref) after a reload, not just within one instance."""
    path = tmp_path / "projects.json"
    store = ProjectStore(persist_path=path)
    first, created_first = store.create(name="a/b", repo_url="https://github.com/a/b", ref=None)
    assert created_first is True

    reloaded = ProjectStore(persist_path=path)
    second, created_second = reloaded.create(name="a/b", repo_url="https://github.com/a/b", ref=None)
    assert created_second is False
    assert second.project_id == first.project_id
