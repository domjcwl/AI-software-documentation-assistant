from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.agents.state import Citation, RetrievedChunk
from app.main import app
from app.services.conversations import conversation_store
from app.services.project_store import project_store


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_project_store():
    """Point the process-wide singleton at no persistence for the
    duration of the test, so API-level tests can never read from or
    write to the real data/projects.json. Persistence itself is tested
    directly against a standalone ProjectStore in test_project_store.py."""
    original_path = project_store._persist_path
    project_store._persist_path = None
    project_store.clear()
    conversation_store.clear()
    yield
    project_store.clear()
    conversation_store.clear()
    project_store._persist_path = original_path


@pytest.fixture(autouse=True)
def _stub_indexing_pipeline(monkeypatch):
    """API-level tests (test_endpoints.py) exercise CRUD/validation only.
    TestClient runs BackgroundTasks synchronously inside client.post(), so
    without this, every POST /projects in the test suite would fire a
    real GitHub API call. The real pipeline has its own tests
    (test_pipeline.py) with fetch/embedding mocked out."""
    monkeypatch.setattr("app.api.projects.run_indexing", lambda project_id: None)


class FakeGraph:
    """Stand-in for the compiled LangGraph, emitting the same
    (mode, chunk) stream shape that app.api.chat consumes. Lets the chat
    endpoint's NDJSON plumbing be tested without any LLM calls."""

    def __init__(self, *, revise_once: bool = False, route: str = "code_qa") -> None:
        self._revise_once = revise_once
        self._route = route

    async def astream(self, state, stream_mode=None):
        chunk = RetrievedChunk(
            path="app/security.py",
            start_line=10,
            end_line=20,
            language="python",
            doc_type="code",
            chunk_index=0,
            body="def verify_password(): ...",
            score=0.81,
        )
        yield "updates", {"coordinator": {"route": self._route, "search_queries": ["auth"]}}
        yield "updates", {"retrieval": {"retrieved": [chunk], "low_confidence": False}}
        yield "messages", (_FakeMessage("Draft "), {"langgraph_node": "explanation"})
        # Another agent's structured-output JSON on the same channel: the
        # endpoint must filter this out rather than stream it to the user.
        yield "messages", (_FakeMessage('{"route":"code_qa"}'), {"langgraph_node": "coordinator"})
        yield "messages", (_FakeMessage("answer."), {"langgraph_node": "explanation"})

        if self._revise_once:
            yield "updates", {"review": {"revision_count": 1, "verdict": _FakeVerdict(["Unsupported claim."])}}
            yield "updates", {"retrieval": {"retrieved": [chunk], "low_confidence": False}}
            yield "messages", (_FakeMessage("Better answer."), {"langgraph_node": "explanation"})

        yield "updates", {
            "review": {
                "final_answer": "Auth works via `app/security.py:10-20`.",
                "citations": [Citation(path="app/security.py", start_line=10, end_line=20)],
                "verdict": _FakeVerdict([], grounded=True),
            }
        }


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeVerdict:
    def __init__(self, issues: list[str], grounded: bool = False) -> None:
        self.issues = issues
        self.grounded = grounded


@pytest.fixture()
def stub_chat_graph(monkeypatch):
    """Opt-in: install a FakeGraph for chat endpoint tests."""

    def _install(**kwargs):
        monkeypatch.setattr("app.api.chat.build_graph", lambda *a, **kw: FakeGraph(**kwargs))
        monkeypatch.setattr("app.api.chat.Embedder", lambda settings: object())

    return _install
