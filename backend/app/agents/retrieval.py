"""Retrieval agent: multi-query search, rank fusion, neighbour expansion.
See planning/architecture.md §4.4.

The `directory` route never reaches this module — it reads the exact tree
built at index time instead (ADR-006). Everything here is approximate
search; when we already have ground truth, we use it.
"""

from __future__ import annotations

import logging

from app.agents.state import GraphState, RetrievedChunk
from app.config import Settings
from app.vectorstore.embedder import Embedder
from app.vectorstore.store import VectorStore

logger = logging.getLogger("app.agents.retrieval")

# Cosine similarity below this means nothing relevant was found, and the
# Explanation agent is told to say so rather than dress up weak matches.
MIN_RELEVANCE = 0.20
_RRF_K = 60  # standard reciprocal-rank-fusion damping constant
_NEIGHBOUR_EXPAND_TOP_N = 3
_CHAR_BUDGET = 24_000  # ~6k tokens, per architecture.md §4.4

# The synthetic documents built at index time (architecture.md §3.4).
_MANIFEST_PATHS = ("__repo_summary__", "__file_index__", "__directory_tree__")


def retrieve(
    state: GraphState, settings: Settings, store: VectorStore, embedder: Embedder
) -> dict:
    project_id = state["project_id"]
    queries = state.get("search_queries") or [state["question"]]

    try:
        query_vectors = embedder.embed_texts(queries)
    except Exception:
        logger.exception("Query embedding failed for project_id=%s", project_id)
        raise

    ranked = _fused_search(store, project_id, query_vectors, settings.retrieval_k)

    if state.get("route") == "architecture":
        ranked = _ensure_manifest_context(store, project_id, query_vectors[0], ranked)

    ranked = _expand_neighbours(store, project_id, ranked)
    chunks = _apply_budget(ranked, settings.max_context_chunks)

    best = max((c.score for c in chunks), default=0.0)
    return {"retrieved": chunks, "low_confidence": best < MIN_RELEVANCE}


def _fused_search(
    store: VectorStore, project_id: str, query_vectors: list[list[float]], k: int
) -> list[RetrievedChunk]:
    """Reciprocal-rank fusion across queries: a chunk ranked decently by
    several different queries beats one ranked highly by a single query,
    which is what makes multi-query worth doing at all."""
    fused: dict[str, float] = {}
    best_similarity: dict[str, float] = {}
    raw: dict[str, dict] = {}

    for vector in query_vectors:
        for rank, hit in enumerate(store.search(project_id, vector, k=k)):
            key = hit["id"]
            fused[key] = fused.get(key, 0.0) + 1.0 / (_RRF_K + rank + 1)
            similarity = _to_similarity(hit["distance"])
            best_similarity[key] = max(best_similarity.get(key, 0.0), similarity)
            raw.setdefault(key, hit)

    ordered = sorted(fused, key=lambda key: fused[key], reverse=True)
    return [_to_chunk(raw[key], best_similarity[key]) for key in ordered]


def _ensure_manifest_context(
    store: VectorStore,
    project_id: str,
    query_vector: list[float],
    ranked: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    """Architecture questions are answered badly by code chunks alone —
    force the repo-summary/file-index documents in even if plain
    similarity didn't surface them (architecture.md §4.3)."""
    present = {c.path for c in ranked}
    forced: list[RetrievedChunk] = []
    for path in ("__repo_summary__", "__file_index__"):
        if path in present:
            continue
        hits = store.search(project_id, query_vector, k=2, where={"path": path})
        forced.extend(_to_chunk(hit, _to_similarity(hit["distance"])) for hit in hits)
    return forced + ranked


def _expand_neighbours(
    store: VectorStore, project_id: str, ranked: list[RetrievedChunk]
) -> list[RetrievedChunk]:
    """Pull chunk_index ±1 for the strongest hits so the model sees a
    function's surroundings instead of a severed fragment.

    Neighbours are spliced in around their anchor, preserving the
    incoming rank-fusion order. Re-sorting the merged list by cosine
    score instead would silently discard the fusion ranking that
    _fused_search just computed — which is the entire point of running
    multiple queries.
    """
    if not ranked:
        return ranked

    seen = {(c.path, c.chunk_index) for c in ranked}
    anchors = {(c.path, c.chunk_index) for c in ranked[:_NEIGHBOUR_EXPAND_TOP_N]}
    expanded: list[RetrievedChunk] = []

    for chunk in ranked:
        is_anchor = (chunk.path, chunk.chunk_index) in anchors and chunk.path not in _MANIFEST_PATHS
        if not is_anchor:
            expanded.append(chunk)
            continue

        before = _fetch_neighbour(store, project_id, chunk, chunk.chunk_index - 1, seen)
        after = _fetch_neighbour(store, project_id, chunk, chunk.chunk_index + 1, seen)
        # Source order around the anchor, so the model reads the file
        # top-to-bottom rather than in retrieval order.
        expanded.extend(c for c in (before, chunk, after) if c is not None)

    return expanded


def _fetch_neighbour(
    store: VectorStore,
    project_id: str,
    anchor: RetrievedChunk,
    neighbour_index: int,
    seen: set[tuple[str, int]],
) -> RetrievedChunk | None:
    if neighbour_index < 0 or (anchor.path, neighbour_index) in seen:
        return None
    hit = store.get_chunk(project_id, f"{anchor.path}::{neighbour_index}")
    if hit is None:
        return None
    seen.add((anchor.path, neighbour_index))
    # Scored just under its anchor so an expansion never outranks a real
    # search hit if something downstream does sort by score.
    return _to_chunk(hit, max(anchor.score - 0.01, 0.0))


def _apply_budget(ranked: list[RetrievedChunk], max_chunks: int) -> list[RetrievedChunk]:
    kept: list[RetrievedChunk] = []
    used = 0
    for chunk in ranked[:max_chunks]:
        cost = len(chunk.body)
        if kept and used + cost > _CHAR_BUDGET:
            break
        kept.append(chunk)
        used += cost
    return kept


def _to_similarity(distance: float | None) -> float:
    """Chroma collections are created with cosine space (see
    app.vectorstore.store.VectorStore.add), so similarity is 1 - distance,
    clamped because floating-point noise can push it just outside [0, 1]."""
    if distance is None:
        return 0.0
    return max(0.0, min(1.0, 1.0 - distance))


def _to_chunk(hit: dict, score: float) -> RetrievedChunk:
    meta = hit.get("metadata") or {}
    return RetrievedChunk(
        path=meta.get("path", "unknown"),
        start_line=int(meta.get("start_line", 1)),
        end_line=int(meta.get("end_line", 1)),
        language=meta.get("language", "text"),
        doc_type=meta.get("doc_type", "code"),
        chunk_index=int(meta.get("chunk_index", 0)),
        body=hit.get("document") or "",
        score=score,
    )


def format_context(chunks: list[RetrievedChunk]) -> str:
    """Render retrieved chunks for the Explanation/Review prompts. The
    `path:start-end` header doubles as the exact citation format those
    agents are told to reproduce."""
    if not chunks:
        return "(no relevant context was retrieved from this repository)"
    blocks = []
    for chunk in chunks:
        blocks.append(
            f"--- {chunk.citation_label()} [{chunk.language}] ---\n{chunk.body}"
        )
    return "\n\n".join(blocks)
