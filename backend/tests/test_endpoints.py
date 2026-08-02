from __future__ import annotations

import json

from app.services.project_store import project_store


def test_health_is_reachable(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "openai_configured" in body
    assert "chroma_ok" in body
    assert isinstance(body["projects_indexed"], int)


def test_create_project_returns_202(client):
    resp = client.post("/projects", json={"repo_url": "https://github.com/tiangolo/fastapi"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["name"] == "tiangolo/fastapi"
    assert body["status"] == "queued"
    assert body["project_id"]


def test_create_project_dedupes_same_repo(client):
    first = client.post("/projects", json={"repo_url": "https://github.com/a/b"}).json()
    second = client.post("/projects", json={"repo_url": "https://github.com/a/b"}).json()
    assert first["project_id"] == second["project_id"]


def test_create_project_rejects_invalid_url(client):
    resp = client.post("/projects", json={"repo_url": "not a url"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_repo_url"


def test_create_project_rejects_non_github_host(client):
    resp = client.post("/projects", json={"repo_url": "https://gitlab.com/a/b"})
    assert resp.status_code == 400


def test_list_projects(client):
    client.post("/projects", json={"repo_url": "https://github.com/a/b"})
    resp = client.get("/projects")
    assert resp.status_code == 200
    projects = resp.json()["projects"]
    assert len(projects) == 1
    assert projects[0]["repo_url"] == "https://github.com/a/b"


def test_get_project_detail(client):
    created = client.post("/projects", json={"repo_url": "https://github.com/a/b"}).json()
    resp = client.get(f"/projects/{created['project_id']}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["status"] == "queued"
    assert detail["percent"] == 0


def test_get_project_not_found(client):
    resp = client.get("/projects/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"


def test_delete_project_then_get_returns_404(client):
    created = client.post("/projects", json={"repo_url": "https://github.com/a/b"}).json()
    del_resp = client.delete(f"/projects/{created['project_id']}")
    assert del_resp.status_code == 204
    get_resp = client.get(f"/projects/{created['project_id']}")
    assert get_resp.status_code == 404


def test_delete_project_is_idempotent(client):
    resp = client.delete("/projects/never-existed")
    assert resp.status_code == 204


def test_chat_returns_404_for_unknown_project(client):
    resp = client.post("/chat", json={"project_id": "nope", "message": "hi"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "project_not_found"


def test_chat_returns_409_when_project_not_ready(client):
    created = client.post("/projects", json={"repo_url": "https://github.com/a/b"}).json()
    resp = client.post("/chat", json={"project_id": created["project_id"], "message": "hi"})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "project_not_ready"


def test_chat_streams_ndjson_when_project_is_ready(client, stub_chat_graph):
    stub_chat_graph()
    created = client.post("/projects", json={"repo_url": "https://github.com/a/b"}).json()
    project_store.update(created["project_id"], status="ready", percent=100)

    resp = client.post(
        "/chat", json={"project_id": created["project_id"], "message": "How does auth work?"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")

    lines = [json.loads(line) for line in resp.text.strip().splitlines()]
    assert lines[0]["type"] == "phase"
    assert lines[-1]["type"] == "final"
    assert any(e["type"] == "token" for e in lines)
