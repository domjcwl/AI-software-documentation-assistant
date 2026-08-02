"""Chat endpoint event-protocol tests. The graph itself is faked (see
conftest.FakeGraph) — these assert the NDJSON contract in
planning/api_contract.md, not agent behaviour."""

from __future__ import annotations

import json

from app.services.conversations import conversation_store
from app.services.project_store import project_store


def _ready_project(client) -> str:
    created = client.post("/projects", json={"repo_url": "https://github.com/a/b"}).json()
    project_store.update(created["project_id"], status="ready", percent=100)
    return created["project_id"]


def _stream(client, project_id: str, message: str = "How does auth work?") -> list[dict]:
    resp = client.post("/chat", json={"project_id": project_id, "message": message})
    assert resp.status_code == 200
    return [json.loads(line) for line in resp.text.strip().splitlines()]


def test_event_ordering_matches_contract(client, stub_chat_graph):
    stub_chat_graph()
    events = _stream(client, _ready_project(client))
    types = [e["type"] for e in events]

    assert types[0] == "phase" and events[0]["phase"] == "coordinating"
    assert types.count("final") == 1
    assert types[-1] == "final"
    assert types.index("route") < types.index("sources")
    assert types.index("sources") < types.index("token")


def test_only_explanation_node_tokens_are_streamed(client, stub_chat_graph):
    """Guards the filter that keeps other agents' structured-output JSON
    out of the user-visible answer — the failure mode found in the T4.0
    spike, where a coordinator's raw JSON streamed as answer text."""
    stub_chat_graph()
    events = _stream(client, _ready_project(client))
    tokens = "".join(e["text"] for e in events if e["type"] == "token")

    assert tokens == "Draft answer."
    assert '{"route"' not in tokens


def test_sources_and_final_payload_shape(client, stub_chat_graph):
    stub_chat_graph()
    events = _stream(client, _ready_project(client))

    sources = next(e for e in events if e["type"] == "sources")
    assert sources["sources"][0]["path"] == "app/security.py"
    assert sources["sources"][0]["start_line"] == 10
    assert "snippet" in sources["sources"][0]

    final = events[-1]
    assert final["citations"] == [{"path": "app/security.py", "start_line": 10, "end_line": 20}]
    assert final["grounded"] is True
    assert final["route"] == "code_qa"
    assert final["revisions"] == 0


def test_revision_emits_revision_event_before_new_tokens(client, stub_chat_graph):
    stub_chat_graph(revise_once=True)
    events = _stream(client, _ready_project(client))
    types = [e["type"] for e in events]

    assert "revision" in types
    revision = next(e for e in events if e["type"] == "revision")
    assert revision["attempt"] == 1
    assert "Unsupported claim." in revision["reason"]
    # New tokens must follow the revision event so the UI knows to discard
    # the draft it already rendered (ADR-005).
    assert types.index("revision") < len(types) - 1 - types[::-1].index("token")
    assert events[-1]["type"] == "final"


def test_conversation_history_is_recorded(client, stub_chat_graph):
    stub_chat_graph()
    project_id = _ready_project(client)
    _stream(client, project_id, "How does auth work?")

    history = conversation_store.get(project_id, "default")
    assert [t.role for t in history] == ["user", "assistant"]
    assert history[0].content == "How does auth work?"
    assert "app/security.py" in history[1].content


def test_separate_conversations_do_not_share_history(client, stub_chat_graph):
    stub_chat_graph()
    project_id = _ready_project(client)
    client.post("/chat", json={"project_id": project_id, "conversation_id": "c1", "message": "one"})
    client.post("/chat", json={"project_id": project_id, "conversation_id": "c2", "message": "two"})

    assert [t.content for t in conversation_store.get(project_id, "c1") if t.role == "user"] == ["one"]
    assert [t.content for t in conversation_store.get(project_id, "c2") if t.role == "user"] == ["two"]


def test_deleting_project_clears_its_conversations(client, stub_chat_graph):
    stub_chat_graph()
    project_id = _ready_project(client)
    _stream(client, project_id)
    assert conversation_store.get(project_id, "default")

    client.delete(f"/projects/{project_id}")
    assert conversation_store.get(project_id, "default") == []
