"""Chroma runs fully locally, so these hit a real PersistentClient
against a temp directory rather than mocking it — faster and more
trustworthy than mocking a database."""

from __future__ import annotations

from app.config import Settings
from app.vectorstore.store import VectorStore


def _store(tmp_path) -> VectorStore:
    settings = Settings(_env_file=None, chroma_dir=tmp_path / "chroma")
    return VectorStore(settings)


def test_search_on_project_with_no_collection_returns_empty(tmp_path):
    store = _store(tmp_path)
    assert store.search("nope", [0.1, 0.2, 0.3]) == []
    assert store.count("nope") == 0


def test_add_then_search_roundtrip(tmp_path):
    store = _store(tmp_path)
    store.add(
        "proj1",
        ids=["a.py::0", "b.py::0"],
        embeddings=[[1.0, 0.0], [0.0, 1.0]],
        documents=["def a(): pass", "def b(): pass"],
        metadatas=[{"path": "a.py", "chunk_index": 0}, {"path": "b.py", "chunk_index": 0}],
    )

    assert store.count("proj1") == 2

    results = store.search("proj1", [1.0, 0.0], k=1)
    assert len(results) == 1
    assert results[0]["id"] == "a.py::0"
    assert results[0]["metadata"]["path"] == "a.py"
    assert results[0]["document"] == "def a(): pass"


def test_search_respects_where_filter(tmp_path):
    store = _store(tmp_path)
    store.add(
        "proj1",
        ids=["a.py::0", "b.md::0"],
        embeddings=[[1.0, 0.0], [1.0, 0.0]],  # identical vectors, differ only by metadata
        documents=["code", "doc"],
        metadatas=[{"doc_type": "code"}, {"doc_type": "doc"}],
    )

    results = store.search("proj1", [1.0, 0.0], k=5, where={"doc_type": "doc"})
    assert len(results) == 1
    assert results[0]["metadata"]["doc_type"] == "doc"


def test_delete_collection_is_idempotent(tmp_path):
    store = _store(tmp_path)
    store.delete_collection("never-existed")  # must not raise

    store.add("proj1", ids=["x::0"], embeddings=[[0.1]], documents=["x"], metadatas=[{"path": "x"}])
    assert store.count("proj1") == 1
    store.delete_collection("proj1")
    assert store.count("proj1") == 0
    store.delete_collection("proj1")  # deleting again is still a no-op success


def test_projects_are_isolated_in_separate_collections(tmp_path):
    store = _store(tmp_path)
    store.add("proj1", ids=["x::0"], embeddings=[[0.1]], documents=["x"], metadatas=[{"path": "x"}])
    store.add("proj2", ids=["x::0"], embeddings=[[0.1]], documents=["y"], metadatas=[{"path": "y"}])

    assert store.count("proj1") == 1
    assert store.count("proj2") == 1
    store.delete_collection("proj1")
    assert store.count("proj1") == 0
    assert store.count("proj2") == 1  # unaffected by proj1's deletion
