"""Thin wrapper around a per-project Chroma collection. See
planning/decisions.md ADR-001 for why Chroma was chosen over FAISS.

API surface (PersistentClient, get_or_create_collection/get_collection,
add/query/delete, chromadb.errors.NotFoundError) verified against the
installed chromadb==1.5.9 rather than assumed.

embedding_function is always passed as None: this store never lets Chroma
compute embeddings itself — every add() and search() call supplies
precomputed OpenAI vectors, so Chroma's bundled default model is never
invoked (and therefore never needs to be downloaded).
"""

from __future__ import annotations

import chromadb
from chromadb.errors import NotFoundError

from app.config import Settings, get_settings

_COLLECTION_PREFIX = "proj_"


def collection_name(project_id: str) -> str:
    return f"{_COLLECTION_PREFIX}{project_id}"


class VectorStore:
    def __init__(self, settings: Settings) -> None:
        settings.chroma_dir.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(settings.chroma_dir))

    def add(
        self,
        project_id: str,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict],
    ) -> None:
        if not ids:
            return
        collection = self._client.get_or_create_collection(
            collection_name(project_id),
            embedding_function=None,
            # Cosine, not Chroma's default L2, so `distance` maps to a
            # directly interpretable similarity (1 - distance) that the
            # retrieval agent's MIN_RELEVANCE threshold can be reasoned
            # about in the abstract rather than tuned by trial and error.
            configuration={"hnsw": {"space": "cosine"}},
        )
        collection.add(ids=ids, embeddings=embeddings, documents=documents, metadatas=metadatas)

    def search(
        self,
        project_id: str,
        query_embedding: list[float],
        k: int = 8,
        where: dict | None = None,
    ) -> list[dict]:
        """Best match first: [{id, document, metadata, distance}, ...].
        Returns [] for a project with no collection yet, rather than
        raising — an empty search result is a normal, answerable state."""
        collection = self._get_existing(project_id)
        if collection is None:
            return []
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids = result["ids"][0] if result.get("ids") else []
        docs = result["documents"][0] if result.get("documents") else []
        metas = result["metadatas"][0] if result.get("metadatas") else []
        dists = result["distances"][0] if result.get("distances") else []
        return [
            {"id": i, "document": d, "metadata": m, "distance": dist}
            for i, d, m, dist in zip(ids, docs, metas, dists)
        ]

    def get_chunk(self, project_id: str, chunk_id: str) -> dict | None:
        """Fetch one chunk by id, for the retrieval agent's neighbour
        expansion. Shaped like a search hit but with distance=None —
        there is no query to be similar to."""
        collection = self._get_existing(project_id)
        if collection is None:
            return None
        result = collection.get(ids=[chunk_id], include=["documents", "metadatas"])
        ids = result.get("ids") or []
        if not ids:
            return None
        documents = result.get("documents") or [""]
        metadatas = result.get("metadatas") or [{}]
        return {
            "id": ids[0],
            "document": documents[0],
            "metadata": metadatas[0],
            "distance": None,
        }

    def delete_collection(self, project_id: str) -> None:
        """Idempotent: deleting an already-gone (or never-created)
        collection is a no-op success, matching ProjectStore.delete."""
        try:
            self._client.delete_collection(collection_name(project_id))
        except NotFoundError:
            pass

    def count(self, project_id: str) -> int:
        collection = self._get_existing(project_id)
        return collection.count() if collection is not None else 0

    def _get_existing(self, project_id: str):
        try:
            return self._client.get_collection(collection_name(project_id), embedding_function=None)
        except NotFoundError:
            return None


# Process-wide singleton, opened once against the configured Chroma
# directory — see app.services.project_store.project_store for the same
# pattern applied to the (in-memory) project registry.
vector_store = VectorStore(get_settings())
