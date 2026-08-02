"""HTTP client for the FastAPI backend.

This module is the frontend's only contact with the network — components
call these functions and never touch httpx directly. The function set
mirrors planning/api_contract.md one-to-one:

    health()          GET    /health
    list_projects()   GET    /projects
    get_project()     GET    /projects/{id}
    create_project()  POST   /projects
    delete_project()  DELETE /projects/{id}
    stream_chat()     POST   /chat        (yields decoded NDJSON events)

Backend errors arrive as {"error": {code, message, hint}}; they are raised
as ApiError so callers can render `message` + `hint` without unpacking
JSON or ever showing a traceback.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

import httpx

BASE_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")

# Indexing a large repo can hold the request open for a while; answering a
# question runs four agents plus possible revisions, so it gets longer still.
_TIMEOUT = httpx.Timeout(15.0, read=60.0)
_CHAT_TIMEOUT = httpx.Timeout(15.0, read=300.0)


class ApiError(Exception):
    """A typed backend error, or a transport failure rendered like one."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        self.code = code
        self.message = message
        self.hint = hint
        super().__init__(message)

    @classmethod
    def from_response(cls, response: httpx.Response) -> ApiError:
        try:
            payload = response.json()["error"]
            return cls(payload["code"], payload["message"], payload.get("hint", ""))
        except Exception:
            # Non-JSON or unexpected shape — surface the status rather than
            # pretending we understood it.
            return cls(
                "unexpected_response",
                f"Backend returned HTTP {response.status_code}.",
                "Check the backend logs.",
            )


_UNREACHABLE_HINT = (
    f"Start it with:  uvicorn app.main:app --app-dir backend  (expected at {BASE_URL})"
)


def _unreachable(exc: Exception) -> ApiError:
    return ApiError("backend_unreachable", f"Cannot reach the backend: {exc}", _UNREACHABLE_HINT)


def _get(path: str) -> dict:
    try:
        response = httpx.get(f"{BASE_URL}{path}", timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise _unreachable(exc) from exc
    if response.status_code >= 400:
        raise ApiError.from_response(response)
    return response.json()


def health() -> dict:
    return _get("/health")


def list_projects() -> list[dict]:
    return _get("/projects")["projects"]


def get_project(project_id: str) -> dict:
    return _get(f"/projects/{project_id}")


def create_project(repo_url: str, ref: str | None = None) -> dict:
    try:
        response = httpx.post(
            f"{BASE_URL}/projects", json={"repo_url": repo_url, "ref": ref}, timeout=_TIMEOUT
        )
    except httpx.HTTPError as exc:
        raise _unreachable(exc) from exc
    if response.status_code >= 400:
        raise ApiError.from_response(response)
    return response.json()


def delete_project(project_id: str) -> None:
    try:
        response = httpx.delete(f"{BASE_URL}/projects/{project_id}", timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise _unreachable(exc) from exc
    if response.status_code >= 400 and response.status_code != 404:
        raise ApiError.from_response(response)


def stream_chat(project_id: str, conversation_id: str, message: str) -> Iterator[dict]:
    """Yield decoded NDJSON events from POST /chat.

    Errors raised *before* the stream opens (unknown project, project not
    ready, missing API key) arrive as a normal JSON error response and are
    raised as ApiError. Failures once streaming has begun arrive as an
    `error` event in the stream itself and are yielded like any other event
    — the caller renders them inline, since output may already be on screen.
    """
    payload = {"project_id": project_id, "conversation_id": conversation_id, "message": message}
    try:
        with httpx.stream(
            "POST", f"{BASE_URL}/chat", json=payload, timeout=_CHAT_TIMEOUT
        ) as response:
            if response.status_code >= 400:
                response.read()  # body isn't loaded yet on a streamed response
                raise ApiError.from_response(response)
            for line in response.iter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue  # a malformed line must not kill an otherwise good answer
    except httpx.HTTPError as exc:
        raise _unreachable(exc) from exc
