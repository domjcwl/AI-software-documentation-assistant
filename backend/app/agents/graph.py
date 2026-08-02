"""LangGraph wiring for the four-agent pipeline.
See planning/architecture.md §4.1 for the diagram this implements.

    START -> coordinator -+-> retrieval -+-> explanation -> review -+-> END
                          |              |                          |
                          +-> directory -+                          +-> retrieval (revise, max 2)
                          |
                          +-> clarify -> END

Node names are part of the public contract with app.api.chat, which
filters LangGraph's message stream on `langgraph_node == "explanation"`
to isolate user-visible tokens from the other agents' structured-output
JSON. Renaming a node here means updating that filter.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents import coordinator, explanation, retrieval, review
from app.agents.state import GraphState
from app.config import Settings
from app.vectorstore.embedder import Embedder
from app.vectorstore.store import VectorStore

NODE_COORDINATOR = "coordinator"
NODE_RETRIEVAL = "retrieval"
NODE_DIRECTORY = "directory"
NODE_CLARIFY = "clarify"
NODE_EXPLANATION = "explanation"
NODE_REVIEW = "review"


def build_graph(settings: Settings, store: VectorStore, embedder: Embedder, project_name: str):
    """Compile the agent graph. Dependencies are closed over rather than
    read from module globals so tests can inject fakes (see
    backend/tests/test_agents.py)."""

    def coordinator_node(state: GraphState) -> dict:
        return coordinator.coordinate(state, settings, project_name)

    def retrieval_node(state: GraphState) -> dict:
        return retrieval.retrieve(state, settings, store, embedder)

    def directory_node(state: GraphState) -> dict:
        return explanation.load_directory_tree(state["project_id"], settings)

    def clarify_node(state: GraphState) -> dict:
        question = (
            state.get("clarifying_question")
            or "Could you be more specific about which part of the code you mean?"
        )
        return {"final_answer": question, "citations": [], "retrieved": []}

    async def explanation_node(state: GraphState) -> dict:
        return await explanation.explain(state, settings, project_name)

    def review_node(state: GraphState) -> dict:
        return review.review(state, settings)

    builder = StateGraph(GraphState)
    builder.add_node(NODE_COORDINATOR, coordinator_node)
    builder.add_node(NODE_RETRIEVAL, retrieval_node)
    builder.add_node(NODE_DIRECTORY, directory_node)
    builder.add_node(NODE_CLARIFY, clarify_node)
    builder.add_node(NODE_EXPLANATION, explanation_node)
    builder.add_node(NODE_REVIEW, review_node)

    builder.add_edge(START, NODE_COORDINATOR)
    builder.add_conditional_edges(
        NODE_COORDINATOR,
        _route_from_coordinator,
        {
            NODE_RETRIEVAL: NODE_RETRIEVAL,
            NODE_DIRECTORY: NODE_DIRECTORY,
            NODE_CLARIFY: NODE_CLARIFY,
        },
    )
    builder.add_edge(NODE_RETRIEVAL, NODE_EXPLANATION)
    builder.add_edge(NODE_DIRECTORY, NODE_EXPLANATION)
    builder.add_edge(NODE_CLARIFY, END)
    builder.add_edge(NODE_EXPLANATION, NODE_REVIEW)
    builder.add_conditional_edges(
        NODE_REVIEW, _route_from_review, {NODE_RETRIEVAL: NODE_RETRIEVAL, END: END}
    )

    return builder.compile()


def _route_from_coordinator(state: GraphState) -> str:
    route = state.get("route", "code_qa")
    if route == "clarify":
        return NODE_CLARIFY
    if route == "directory":
        return NODE_DIRECTORY
    return NODE_RETRIEVAL


def _route_from_review(state: GraphState) -> str:
    """Review signals "revise" by leaving final_answer unset — it only
    populates it on the finalise path, so this needs no separate flag."""
    return END if state.get("final_answer") else NODE_RETRIEVAL


def initial_state(project_id: str, conversation_id: str, question: str, history) -> GraphState:
    return {
        "project_id": project_id,
        "conversation_id": conversation_id,
        "question": question,
        "history": history,
        "revision_count": 0,
        "retrieved": [],
        "citations": [],
        "low_confidence": False,
        "verdict": None,
        "error": None,
    }
