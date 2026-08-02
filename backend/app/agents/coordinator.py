"""Coordinator agent: classify the question and plan retrieval.
See planning/architecture.md §4.3.

This is the only place conversation history is turned into self-contained
search queries — without that rewriting step, a follow-up like "explain
this function" retrieves nothing useful, because the referent lives in
the previous turn rather than in the question itself.
"""

from __future__ import annotations

import logging

from app.agents import prompts
from app.agents.llm import fast_model
from app.agents.state import CoordinatorPlan, GraphState, Turn
from app.config import Settings

logger = logging.getLogger("app.agents.coordinator")

_HISTORY_TURNS = 6  # 3 exchanges


def coordinate(state: GraphState, settings: Settings, project_name: str) -> dict:
    question = state["question"]
    history = state.get("history") or []

    planner = fast_model(settings).with_structured_output(CoordinatorPlan)
    messages = [
        ("system", prompts.COORDINATOR_SYSTEM.format(project_name=project_name)),
        (
            "user",
            prompts.COORDINATOR_USER.format(
                history=format_history(history) or "(no previous turns)", question=question
            ),
        ),
    ]

    try:
        plan: CoordinatorPlan = planner.invoke(messages)
    except Exception:
        # A routing failure should degrade to a plain search, not sink the
        # whole request — code_qa with the raw question is a usable default.
        logger.exception("Coordinator failed; falling back to code_qa with the raw question")
        return {"route": "code_qa", "search_queries": [question], "clarifying_question": None}

    queries = [q.strip() for q in plan.search_queries if q.strip()]
    if plan.route not in ("clarify", "directory") and not queries:
        queries = [question]

    return {
        "route": plan.route,
        "search_queries": queries,
        "clarifying_question": plan.clarifying_question,
    }


def format_history(history: list[Turn], limit: int = _HISTORY_TURNS) -> str:
    if not history:
        return ""
    recent = history[-limit:]
    return "\n".join(f"{turn.role}: {turn.content}" for turn in recent)
