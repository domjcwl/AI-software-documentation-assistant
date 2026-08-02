"""Agent-internals tests. Every LLM call is faked — these cover the
deterministic logic around the models (routing fallbacks, rank fusion,
neighbour expansion, citation validation, the revision loop), which is
where the real bugs live."""

from __future__ import annotations

import pytest

from app.agents import coordinator, retrieval, review
from app.agents.graph import _route_from_coordinator, _route_from_review, build_graph
from app.agents.state import (
    Citation,
    CoordinatorPlan,
    GraphState,
    RetrievedChunk,
    ReviewVerdict,
    Turn,
)
from app.config import Settings


def _settings(**overrides) -> Settings:
    overrides.setdefault("openai_api_key", "sk-test")
    return Settings(_env_file=None, **overrides)


def _chunk(path="app/auth.py", idx=0, score=0.8, start=1, end=10) -> RetrievedChunk:
    return RetrievedChunk(
        path=path,
        start_line=start,
        end_line=end,
        language="python",
        doc_type="code",
        chunk_index=idx,
        body=f"code from {path} chunk {idx}",
        score=score,
    )


# --- Coordinator -----------------------------------------------------------


class _FakePlanner:
    def __init__(self, plan=None, raises=False):
        self._plan = plan
        self._raises = raises
        self.last_messages = None

    def invoke(self, messages):
        self.last_messages = messages
        if self._raises:
            raise RuntimeError("model unavailable")
        return self._plan


def _patch_planner(monkeypatch, planner):
    class _Model:
        def with_structured_output(self, schema):
            return planner

    monkeypatch.setattr(coordinator, "fast_model", lambda settings: _Model())


def test_coordinator_returns_plan(monkeypatch):
    plan = CoordinatorPlan(route="architecture", search_queries=["project structure", "entry point"])
    _patch_planner(monkeypatch, _FakePlanner(plan))

    result = coordinator.coordinate({"question": "what is this?", "history": []}, _settings(), "a/b")

    assert result["route"] == "architecture"
    assert result["search_queries"] == ["project structure", "entry point"]


def test_coordinator_falls_back_to_code_qa_when_model_fails(monkeypatch):
    _patch_planner(monkeypatch, _FakePlanner(raises=True))

    result = coordinator.coordinate({"question": "how does auth work?", "history": []}, _settings(), "a/b")

    assert result["route"] == "code_qa"
    assert result["search_queries"] == ["how does auth work?"]


def test_coordinator_backfills_empty_queries(monkeypatch):
    _patch_planner(monkeypatch, _FakePlanner(CoordinatorPlan(route="code_qa", search_queries=[])))

    result = coordinator.coordinate({"question": "what is X?", "history": []}, _settings(), "a/b")

    assert result["search_queries"] == ["what is X?"]


def test_coordinator_does_not_backfill_queries_for_clarify(monkeypatch):
    plan = CoordinatorPlan(route="clarify", search_queries=[], clarifying_question="Which function?")
    _patch_planner(monkeypatch, _FakePlanner(plan))

    result = coordinator.coordinate({"question": "explain this", "history": []}, _settings(), "a/b")

    assert result["search_queries"] == []
    assert result["clarifying_question"] == "Which function?"


def test_coordinator_passes_history_into_the_prompt(monkeypatch):
    planner = _FakePlanner(CoordinatorPlan(route="code_qa", search_queries=["q"]))
    _patch_planner(monkeypatch, planner)
    history = [Turn(role="user", content="what does validate_token do?"),
               Turn(role="assistant", content="It checks the JWT.")]

    coordinator.coordinate({"question": "explain this function", "history": history}, _settings(), "a/b")

    prompt = planner.last_messages[1][1]
    assert "validate_token" in prompt, "history must reach the coordinator for reference resolution"


def test_format_history_truncates_to_recent_turns():
    history = [Turn(role="user", content=f"msg{i}") for i in range(20)]
    formatted = coordinator.format_history(history, limit=4)
    assert "msg19" in formatted
    assert "msg0" not in formatted


# --- Retrieval -------------------------------------------------------------


class _FakeStore:
    def __init__(self, results_by_query=None, chunks_by_id=None):
        self._results = results_by_query or []
        self._chunks = chunks_by_id or {}
        self.calls = 0

    def search(self, project_id, vector, k=8, where=None):
        self.calls += 1
        idx = min(self.calls - 1, len(self._results) - 1) if self._results else 0
        return self._results[idx] if self._results else []

    def get_chunk(self, project_id, chunk_id):
        return self._chunks.get(chunk_id)


class _FakeEmbedder:
    def embed_texts(self, texts, on_batch=None):
        return [[0.1, 0.2] for _ in texts]


def _hit(path, idx, distance, start=1, end=10):
    return {
        "id": f"{path}::{idx}",
        "document": f"code from {path} chunk {idx}",
        "metadata": {
            "path": path,
            "start_line": start,
            "end_line": end,
            "language": "python",
            "doc_type": "code",
            "chunk_index": idx,
        },
        "distance": distance,
    }


def test_retrieval_fuses_ranks_across_queries():
    # b appears in both queries at decent rank; a is only top of query 1.
    store = _FakeStore(results_by_query=[
        [_hit("a.py", 0, 0.2), _hit("b.py", 0, 0.3)],
        [_hit("b.py", 0, 0.25), _hit("c.py", 0, 0.4)],
    ])
    state: GraphState = {"project_id": "p", "question": "q", "search_queries": ["q1", "q2"]}

    result = retrieval.retrieve(state, _settings(), store, _FakeEmbedder())
    paths = [c.path for c in result["retrieved"]]

    assert paths[0] == "b.py", "a chunk found by multiple queries should win rank fusion"
    assert set(paths) == {"a.py", "b.py", "c.py"}


def test_retrieval_converts_cosine_distance_to_similarity():
    store = _FakeStore(results_by_query=[[_hit("a.py", 0, 0.25)]])
    state: GraphState = {"project_id": "p", "question": "q", "search_queries": ["q1"]}

    result = retrieval.retrieve(state, _settings(), store, _FakeEmbedder())

    assert result["retrieved"][0].score == pytest.approx(0.75)


def test_retrieval_flags_low_confidence_on_weak_matches():
    store = _FakeStore(results_by_query=[[_hit("a.py", 0, 0.95)]])  # similarity 0.05
    state: GraphState = {"project_id": "p", "question": "q", "search_queries": ["q1"]}

    result = retrieval.retrieve(state, _settings(), store, _FakeEmbedder())

    assert result["low_confidence"] is True


def test_retrieval_empty_results_are_low_confidence():
    state: GraphState = {"project_id": "p", "question": "q", "search_queries": ["q1"]}

    result = retrieval.retrieve(state, _settings(), _FakeStore(), _FakeEmbedder())

    assert result["retrieved"] == []
    assert result["low_confidence"] is True


def test_retrieval_expands_neighbours():
    store = _FakeStore(
        results_by_query=[[_hit("a.py", 1, 0.1)]],
        chunks_by_id={
            "a.py::0": _hit("a.py", 0, None),
            "a.py::2": _hit("a.py", 2, None),
        },
    )
    state: GraphState = {"project_id": "p", "question": "q", "search_queries": ["q1"]}

    result = retrieval.retrieve(state, _settings(), store, _FakeEmbedder())
    indices = sorted(c.chunk_index for c in result["retrieved"])

    assert indices == [0, 1, 2], "neighbouring chunks should be pulled in around a strong hit"


def test_retrieval_respects_max_context_chunks():
    hits = [_hit(f"f{i}.py", 0, 0.1) for i in range(30)]
    store = _FakeStore(results_by_query=[hits])
    state: GraphState = {"project_id": "p", "question": "q", "search_queries": ["q1"]}

    result = retrieval.retrieve(state, _settings(max_context_chunks=5), store, _FakeEmbedder())

    assert len(result["retrieved"]) <= 5


def test_format_context_includes_citation_labels():
    text = retrieval.format_context([_chunk(path="app/auth.py", start=5, end=9)])
    assert "app/auth.py:5-9" in text


def test_format_context_handles_empty():
    assert "no relevant context" in retrieval.format_context([])


# --- Review: citation extraction ------------------------------------------


def test_extract_citations_parses_ranges_and_single_lines():
    chunks = [_chunk(path="app/auth.py")]
    found = review.extract_citations(
        "See `app/auth.py:10-20` and app/auth.py:33 for details.", chunks
    )
    assert Citation(path="app/auth.py", start_line=10, end_line=20) in found
    assert Citation(path="app/auth.py", start_line=33, end_line=33) in found


def test_extract_citations_rejects_paths_not_retrieved():
    """The core anti-hallucination guard: a confident-looking citation to
    a file that was never retrieved must not reach the user."""
    chunks = [_chunk(path="app/auth.py")]
    found = review.extract_citations("See `app/totally_made_up.py:1-5`.", chunks)
    assert found == []


def test_extract_citations_excludes_synthetic_manifest_docs():
    chunks = [_chunk(path="__repo_summary__")]
    found = review.extract_citations("See `__repo_summary__:1-5`.", chunks)
    assert found == [], "generated summaries are not files a user can open"


def test_extract_citations_dedupes():
    chunks = [_chunk(path="app/auth.py")]
    found = review.extract_citations("`app/auth.py:1-5` ... again `app/auth.py:1-5`", chunks)
    assert len(found) == 1


def test_extract_citations_accepts_bare_paths():
    """The modification route names whole files to change without a line
    range; those still count as citations, attributed to the retrieved span."""
    chunks = [_chunk(path="app/auth.py", start=40, end=88)]
    found = review.extract_citations("Modify `app/auth.py` to add the provider.", chunks)
    assert found == [Citation(path="app/auth.py", start_line=40, end_line=88)]


def test_extract_citations_rejects_bare_path_not_retrieved():
    chunks = [_chunk(path="app/auth.py")]
    found = review.extract_citations("Create `app/brand_new_file.py` for this.", chunks)
    assert found == []


def test_extract_citations_bare_path_spans_multiple_chunks():
    chunks = [
        _chunk(path="app/auth.py", idx=0, start=1, end=30),
        _chunk(path="app/auth.py", idx=1, start=31, end=70),
    ]
    found = review.extract_citations("See `app/auth.py`.", chunks)
    assert found == [Citation(path="app/auth.py", start_line=1, end_line=70)]


# --- Review: verdict handling ---------------------------------------------


class _FakeReviewer:
    def __init__(self, verdict=None, raises=False):
        self._verdict = verdict
        self._raises = raises

    def invoke(self, messages):
        if self._raises:
            raise RuntimeError("reviewer down")
        return self._verdict


def _patch_reviewer(monkeypatch, reviewer):
    class _Model:
        def with_structured_output(self, schema):
            return reviewer

    monkeypatch.setattr(review, "fast_model", lambda settings: _Model())


def _review_state(draft: str, revision_count: int = 0) -> GraphState:
    return {
        "question": "how does auth work?",
        "draft_answer": draft,
        "retrieved": [_chunk(path="app/auth.py", start=1, end=10)],
        "route": "code_qa",
        "revision_count": revision_count,
    }


def test_review_finalises_grounded_answer(monkeypatch):
    _patch_reviewer(monkeypatch, _FakeReviewer(ReviewVerdict(grounded=True, cites_files=True)))

    result = review.review(_review_state("Auth is in `app/auth.py:1-10`."), _settings())

    assert result["final_answer"] == "Auth is in `app/auth.py:1-10`."
    assert result["citations"] == [Citation(path="app/auth.py", start_line=1, end_line=10)]


def test_review_requests_revision_when_ungrounded(monkeypatch):
    verdict = ReviewVerdict(
        grounded=False, cites_files=True, issues=["Claim not supported."], suggested_queries=["better query"]
    )
    _patch_reviewer(monkeypatch, _FakeReviewer(verdict))

    result = review.review(_review_state("Auth is in `app/auth.py:1-10`."), _settings())

    assert "final_answer" not in result, "a revision request must not finalise an answer"
    assert result["revision_count"] == 1
    assert result["search_queries"] == ["better query"]


def test_review_requests_revision_when_uncited(monkeypatch):
    _patch_reviewer(monkeypatch, _FakeReviewer(ReviewVerdict(grounded=True, cites_files=True)))

    result = review.review(_review_state("Auth works somehow, trust me."), _settings())

    assert "final_answer" not in result
    assert result["revision_count"] == 1


def test_review_accepts_honest_decline_without_citations(monkeypatch):
    """Correctly reporting that the repo lacks something is the desired
    outcome — revising it would only pressure the next draft into
    inventing something, which is the exact failure RAG must avoid."""
    verdict = ReviewVerdict(grounded=True, cites_files=False, declines_for_lack_of_context=True)
    _patch_reviewer(monkeypatch, _FakeReviewer(verdict))

    result = review.review(_review_state("This repository contains no Kubernetes code."), _settings())

    assert "final_answer" in result
    assert result["citations"] == []
    assert "could not be fully verified" not in result["final_answer"]


def test_review_does_not_require_citations_for_directory_route(monkeypatch):
    """The directory route's context is the complete file tree, not
    retrieved chunks (ADR-006), so there is nothing to validate against."""
    _patch_reviewer(monkeypatch, _FakeReviewer(ReviewVerdict(grounded=True, cites_files=False)))
    state = _review_state("The project has src/, tests/ and docs/ directories.")
    state["route"] = "directory"
    state["retrieved"] = []

    result = review.review(state, _settings())

    assert "final_answer" in result, "directory answers must not loop for missing citations"


def test_review_finalises_with_caveat_at_revision_ceiling(monkeypatch):
    verdict = ReviewVerdict(grounded=False, cites_files=False, issues=["Still unsupported."])
    _patch_reviewer(monkeypatch, _FakeReviewer(verdict))

    result = review.review(_review_state("Vague answer.", revision_count=2), _settings(max_revisions=2))

    assert "final_answer" in result, "must finalise at the ceiling rather than loop forever"
    assert "could not be fully verified" in result["final_answer"]


def test_review_survives_reviewer_failure(monkeypatch):
    _patch_reviewer(monkeypatch, _FakeReviewer(raises=True))

    result = review.review(_review_state("Auth is in `app/auth.py:1-10`."), _settings())

    assert result["final_answer"] == "Auth is in `app/auth.py:1-10`."


def test_review_rejects_polish_that_drops_citations(monkeypatch):
    verdict = ReviewVerdict(
        grounded=True, cites_files=True, polished_answer="Much nicer prose with no citations at all."
    )
    _patch_reviewer(monkeypatch, _FakeReviewer(verdict))

    result = review.review(_review_state("Auth is in `app/auth.py:1-10`."), _settings())

    assert result["final_answer"] == "Auth is in `app/auth.py:1-10`."


def test_review_accepts_polish_that_keeps_citations(monkeypatch):
    verdict = ReviewVerdict(
        grounded=True,
        cites_files=True,
        polished_answer="Authentication is handled in `app/auth.py:1-10`.",
    )
    _patch_reviewer(monkeypatch, _FakeReviewer(verdict))

    result = review.review(_review_state("Auth is in `app/auth.py:1-10`."), _settings())

    assert result["final_answer"] == "Authentication is handled in `app/auth.py:1-10`."


def test_review_handles_empty_draft(monkeypatch):
    _patch_reviewer(monkeypatch, _FakeReviewer(ReviewVerdict(grounded=True, cites_files=True)))
    result = review.review(_review_state(""), _settings())
    assert "final_answer" in result
    assert result["citations"] == []


# --- Graph routing ---------------------------------------------------------


@pytest.mark.parametrize(
    "route,expected",
    [
        ("code_qa", "retrieval"),
        ("architecture", "retrieval"),
        ("modification", "retrieval"),
        ("directory", "directory"),
        ("clarify", "clarify"),
    ],
)
def test_coordinator_routing_table(route, expected):
    assert _route_from_coordinator({"route": route}) == expected


def test_review_routing_finalises_when_answer_present():
    from langgraph.graph import END

    assert _route_from_review({"final_answer": "done"}) == END
    assert _route_from_review({}) == "retrieval"


def test_graph_compiles_with_expected_nodes():
    graph = build_graph(_settings(), _FakeStore(), _FakeEmbedder(), "a/b")
    nodes = set(graph.get_graph().nodes)
    assert {"coordinator", "retrieval", "directory", "clarify", "explanation", "review"} <= nodes
