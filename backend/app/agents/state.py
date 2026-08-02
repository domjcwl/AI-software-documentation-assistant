"""Shared graph state and the structured-output schemas the agents return.
See planning/architecture.md §4.2.

The pydantic models here are what get handed to
`ChatOpenAI.with_structured_output(...)`, so their field descriptions are
load-bearing prompt text, not just documentation — the model reads them.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel, Field

Route = Literal["code_qa", "architecture", "directory", "modification", "clarify"]

ROUTE_VALUES: tuple[str, ...] = ("code_qa", "architecture", "directory", "modification", "clarify")


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class RetrievedChunk(BaseModel):
    path: str
    start_line: int
    end_line: int
    language: str
    doc_type: str
    chunk_index: int
    body: str
    score: float  # cosine similarity in [0, 1]; higher is better

    def citation_label(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


class Citation(BaseModel):
    path: str
    start_line: int
    end_line: int


class CoordinatorPlan(BaseModel):
    """Structured output of the Coordinator agent."""

    route: Route = Field(
        description=(
            "Which workflow answers this question. "
            "'code_qa': how something works / explain specific code. "
            "'architecture': high-level project overview, structure, or tech stack. "
            "'directory': specifically about the file/folder layout of the project. "
            "'modification': where and how to change the code to add or alter a feature. "
            "'clarify': the question refers to something with no identifiable referent "
            "(e.g. 'explain this function' with no function named anywhere in the conversation)."
        )
    )
    search_queries: list[str] = Field(
        default_factory=list,
        description=(
            "2-4 self-contained search queries for a code vector database. Resolve all "
            "pronouns and references using the conversation history, so each query makes "
            "sense on its own without any other context. Use terms likely to appear in "
            "source code or documentation, not conversational phrasing. Empty for the "
            "'clarify' and 'directory' routes."
        ),
    )
    clarifying_question: str | None = Field(
        default=None,
        description="Only for the 'clarify' route: the single question to ask the user.",
    )


class ReviewVerdict(BaseModel):
    """Structured output of the Review agent. See planning/architecture.md §4.6."""

    grounded: bool = Field(
        description="True only if every factual claim about the code is supported by the provided context."
    )
    cites_files: bool = Field(
        description="True if the answer cites at least one file path in `path:start-end` form."
    )
    declines_for_lack_of_context: bool = Field(
        default=False,
        description=(
            "True if the draft correctly reports that the repository does not contain what "
            "was asked about, rather than attempting an answer. Such a response is the "
            "desired behaviour, needs no citations, and must not be sent back for revision."
        ),
    )
    issues: list[str] = Field(
        default_factory=list, description="Specific problems found. Empty if the answer is good."
    )
    suggested_queries: list[str] = Field(
        default_factory=list,
        description=(
            "Only when requesting a revision: 2-4 better search queries that would retrieve "
            "the missing context. Empty when the answer is acceptable."
        ),
    )
    polished_answer: str | None = Field(
        default=None,
        description=(
            "Only when the answer is already grounded and cited but could read more clearly: "
            "the full improved answer, preserving every citation exactly. Null otherwise."
        ),
    )


class GraphState(TypedDict, total=False):
    """Mutable state threaded through every node.

    `total=False` because nodes return partial dicts that LangGraph merges
    in — no node ever constructs the whole state.
    """

    project_id: str
    conversation_id: str
    question: str
    history: list[Turn]

    route: Route
    search_queries: list[str]
    clarifying_question: str | None

    retrieved: list[RetrievedChunk]
    low_confidence: bool
    directory_tree: str | None

    draft_answer: str
    verdict: ReviewVerdict | None
    revision_count: int

    final_answer: str
    citations: list[Citation]
    error: str | None
