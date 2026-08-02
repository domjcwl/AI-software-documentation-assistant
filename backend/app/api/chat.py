"""POST /chat — NDJSON streaming answers from the LangGraph agent pipeline.
See planning/api_contract.md for the event protocol and
planning/decisions.md ADR-005 for the streaming/review ordering decision.

Two LangGraph stream modes are consumed together:
  - "messages" gives token-level output, filtered to the explanation node
    (the other agents stream structured-output JSON through the same
    channel — forwarding it unfiltered would emit raw JSON to the user);
  - "updates" gives per-node completion, which drives the phase/route/
    sources/revision events.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.agents.graph import (
    NODE_COORDINATOR,
    NODE_DIRECTORY,
    NODE_EXPLANATION,
    NODE_RETRIEVAL,
    NODE_REVIEW,
    build_graph,
    initial_state,
)
from app.config import get_settings
from app.errors import AppError, ProjectNotReadyError
from app.schemas import ChatRequest
from app.services.conversations import conversation_store
from app.services.project_store import project_store
from app.vectorstore.embedder import Embedder
from app.vectorstore.store import vector_store

logger = logging.getLogger("app.api.chat")

router = APIRouter(tags=["chat"])


@router.post("/chat")
async def chat(payload: ChatRequest) -> StreamingResponse:
    record = project_store.get(payload.project_id)  # raises ProjectNotFoundError
    if record.status != "ready":
        raise ProjectNotReadyError(
            f"Project {record.name!r} is not ready yet "
            f"(status: {record.status}, {record.percent}% complete)."
        )

    settings = get_settings()
    # Constructed before the stream opens so a missing/invalid API key
    # surfaces as a normal JSON error response rather than mid-stream.
    embedder = Embedder(settings)
    conversation_id = payload.conversation_id or "default"

    return StreamingResponse(
        _run_graph(payload, record.name, conversation_id, settings, embedder),
        media_type="application/x-ndjson",
    )


def _event(**fields) -> bytes:
    return (json.dumps(fields) + "\n").encode("utf-8")


async def _run_graph(
    payload: ChatRequest, project_name: str, conversation_id: str, settings, embedder
) -> AsyncIterator[bytes]:
    project_id = payload.project_id
    history = conversation_store.get(project_id, conversation_id)

    try:
        graph = build_graph(settings, vector_store, embedder, project_name)
        state = initial_state(project_id, conversation_id, payload.message, history)

        yield _event(type="phase", phase="coordinating", detail="Understanding the question")

        emitted_explaining_phase = False
        final_state: dict = {}

        async for mode, chunk in graph.astream(state, stream_mode=["updates", "messages"]):
            if mode == "messages":
                message, meta = chunk
                if meta.get("langgraph_node") != NODE_EXPLANATION:
                    continue  # other agents' structured-output JSON — never user-facing
                text = message.content
                if isinstance(text, str) and text:
                    if not emitted_explaining_phase:
                        yield _event(type="phase", phase="explaining", detail="Writing the answer")
                        emitted_explaining_phase = True
                    yield _event(type="token", text=text)
                continue

            for node_name, update in chunk.items():
                if not isinstance(update, dict):
                    continue
                final_state.update(update)

                for event in _events_for_node(node_name, update):
                    if event.get("type") == "revision":
                        emitted_explaining_phase = False
                    yield _event(**event)

        answer = final_state.get("final_answer") or ""
        citations = [c.model_dump() for c in final_state.get("citations") or []]
        verdict = final_state.get("verdict")

        conversation_store.append(project_id, conversation_id, "user", payload.message)
        conversation_store.append(project_id, conversation_id, "assistant", answer)

        yield _event(
            type="final",
            answer=answer,
            citations=citations,
            route=final_state.get("route", "code_qa"),
            revisions=final_state.get("revision_count", 0),
            grounded=bool(getattr(verdict, "grounded", False)),
            low_confidence=bool(final_state.get("low_confidence", False)),
        )

    except AppError as exc:
        logger.warning("Chat failed for project_id=%s: %s", project_id, exc.message)
        yield _event(type="error", error=exc.to_body()["error"])
    except Exception as exc:
        logger.exception("Unexpected chat failure for project_id=%s", project_id)
        yield _event(
            type="error",
            error={
                "code": "internal_error",
                "message": f"The assistant failed to answer: {exc}",
                "hint": "Check server logs for details.",
            },
        )


def _events_for_node(node_name: str, update: dict) -> list[dict]:
    """Translate a completed node's state update into client events."""
    if node_name == NODE_COORDINATOR:
        route = update.get("route", "code_qa")
        events = [
            {"type": "route", "route": route, "queries": update.get("search_queries") or []}
        ]
        if route == "directory":
            events.append(
                {"type": "phase", "phase": "retrieving", "detail": "Reading the project file tree"}
            )
        elif route != "clarify":
            queries = update.get("search_queries") or []
            events.append(
                {
                    "type": "phase",
                    "phase": "retrieving",
                    "detail": f"Searching with {len(queries)} quer{'y' if len(queries) == 1 else 'ies'}",
                }
            )
        return events

    if node_name in (NODE_RETRIEVAL, NODE_DIRECTORY):
        chunks = update.get("retrieved") or []
        return [
            {
                "type": "sources",
                "sources": [
                    {
                        "path": c.path,
                        "start_line": c.start_line,
                        "end_line": c.end_line,
                        "language": c.language,
                        "doc_type": c.doc_type,
                        "score": round(c.score, 4),
                        "snippet": c.body[:600],
                    }
                    for c in chunks
                ],
            }
        ]

    if node_name == NODE_EXPLANATION:
        return [{"type": "phase", "phase": "reviewing", "detail": "Checking the answer"}]

    if node_name == NODE_REVIEW:
        if update.get("final_answer"):
            return []
        verdict = update.get("verdict")
        reason = "; ".join(getattr(verdict, "issues", []) or []) or "Answer needed better support."
        return [
            {"type": "revision", "reason": reason, "attempt": update.get("revision_count", 1)},
            {"type": "phase", "phase": "revising", "detail": "Retrieving additional context"},
        ]

    return []
