"""Explanation agent: the only node whose output the user reads directly,
and the only one that streams. See planning/architecture.md §4.5.

Tokens reach the client because this node calls `.astream()` on the chat
model; the /chat endpoint filters LangGraph's message stream down to this
node by name. That filtering is essential, not cosmetic — the Coordinator
and Review agents stream their structured-output JSON through the same
channel, and forwarding it unfiltered would emit raw JSON fragments into
the user's answer (confirmed during the T4.0 spike).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.agents import prompts
from app.agents.coordinator import format_history
from app.agents.llm import chat_model
from app.agents.retrieval import format_context
from app.agents.state import GraphState
from app.config import Settings

logger = logging.getLogger("app.agents.explanation")

_MAX_TREE_CHARS = 12_000


async def explain(state: GraphState, settings: Settings, project_name: str) -> dict:
    route = state.get("route", "code_qa")
    context = (
        state.get("directory_tree")
        if route == "directory"
        else format_context(state.get("retrieved") or [])
    ) or "(no context available)"

    guidance = prompts.ROUTE_GUIDANCE.get(route, prompts.ROUTE_GUIDANCE["code_qa"])
    if state.get("low_confidence") and route != "directory":
        guidance += prompts.LOW_CONFIDENCE_NOTE

    system = prompts.EXPLANATION_SYSTEM.format(
        project_name=project_name,
        route_guidance=guidance,
        citation_rules=prompts._CITATION_RULES,
    )
    user = prompts.EXPLANATION_USER.format(
        context=context,
        history=format_history(state.get("history") or []) or "(no previous turns)",
        question=state["question"],
    )

    verdict = state.get("verdict")
    if verdict is not None and verdict.issues:
        user += (
            "\n\nA previous draft of this answer was rejected in review for these reasons:\n"
            + "\n".join(f"- {issue}" for issue in verdict.issues)
            + "\nWrite a corrected answer that addresses them using the context above."
        )

    model = chat_model(settings)
    pieces: list[str] = []
    async for chunk in model.astream([("system", system), ("user", user)]):
        text = chunk.content
        if isinstance(text, str) and text:
            pieces.append(text)

    return {"draft_answer": "".join(pieces).strip()}


def load_directory_tree(project_id: str, settings: Settings) -> dict:
    """The `directory` route's stand-in for retrieval: read the exact tree
    persisted at index time rather than searching for it (ADR-006)."""
    path: Path = settings.repo_dir / f"{project_id}.manifest.json"
    if not path.exists():
        logger.warning("No manifest tree for project_id=%s at %s", project_id, path)
        return {"directory_tree": None, "retrieved": [], "low_confidence": True}

    try:
        tree = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read manifest tree for project_id=%s", project_id)
        return {"directory_tree": None, "retrieved": [], "low_confidence": True}

    rendered = _render_tree(tree)
    truncated = len(rendered) > _MAX_TREE_CHARS
    if truncated:
        rendered = rendered[:_MAX_TREE_CHARS] + "\n... (tree truncated for length)"

    header = "The COMPLETE file tree of this repository:"
    if truncated:
        header = "The file tree of this repository (truncated — say so if it matters):"

    return {"directory_tree": f"{header}\n\n{rendered}", "retrieved": [], "low_confidence": False}


def _render_tree(node: dict, prefix: str = "") -> str:
    lines: list[str] = []
    for child in node.get("children", []):
        if child.get("type") == "dir":
            lines.append(
                f"{prefix}{child['name']}/  ({child.get('file_count', 0)} files, "
                f"{child.get('total_lines', 0)} lines)"
            )
            nested = _render_tree(child, prefix + "  ")
            if nested:
                lines.append(nested)
        else:
            lines.append(f"{prefix}{child['name']}  ({child.get('lines', 0)} lines)")
    return "\n".join(lines)
