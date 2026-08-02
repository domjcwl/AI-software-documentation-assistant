"""Orchestrates one full indexing job: fetch -> scan -> chunk (+ manifest
docs) -> embed -> store. See planning/architecture.md §3 for the stage
design and §3.6 for the progress-weighting model implemented here.

Meant to run via FastAPI BackgroundTasks, which executes a sync callable
in a thread pool (planning/decisions.md ADR-009) — this module is
intentionally synchronous throughout rather than async.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

from app.config import Settings, get_settings
from app.errors import AppError, NoIndexableFilesError
from app.ingestion import github, manifest, scanner
from app.ingestion.chunker import Chunk, chunk_text, read_source_text
from app.ingestion.manifest import ManifestDocs
from app.ingestion.scanner import ScannedFile
from app.schemas import ErrorDetail
from app.services.project_store import ProjectStore, project_store
from app.services.repo_url import parse_repo_url
from app.vectorstore.embedder import Embedder
from app.vectorstore.store import VectorStore, vector_store

logger = logging.getLogger("app.ingestion.pipeline")

# Weighted so the progress bar advances smoothly instead of sitting at 0
# through the (usually slowest) embedding phase. Sums to 100.
_W_FETCH = 10
_W_SCAN = 10
_W_CHUNK = 15
_W_EMBED = 65


def run_indexing(
    project_id: str,
    *,
    store: ProjectStore = project_store,
    vectors: VectorStore = vector_store,
) -> None:
    """Background-task entry point. Never raises — every failure path is
    caught and recorded on the project record, since nothing awaits or
    inspects this function's return value."""
    settings = get_settings()
    try:
        record = store.get(project_id)
    except AppError:
        logger.error("run_indexing called for unknown project_id=%s", project_id)
        return

    try:
        _run(project_id, record.repo_url, record.ref, settings, store, vectors)
    except AppError as exc:
        logger.warning("Indexing failed for project_id=%s: %s", project_id, exc.message)
        _fail(project_id, settings, store, vectors, ErrorDetail(code=exc.code, message=exc.message, hint=exc.hint))
    except Exception as exc:  # pragma: no cover - defensive catch-all, see app.errors philosophy
        logger.exception("Unexpected error indexing project_id=%s", project_id)
        _fail(
            project_id, settings, store, vectors,
            ErrorDetail(code="internal_error", message=str(exc), hint="Check server logs."),
        )


def _fail(project_id: str, settings: Settings, store: ProjectStore, vectors: VectorStore, error: ErrorDetail) -> None:
    """A failed job leaves no orphaned collection or partial repo on disk."""
    store.update(project_id, status="failed", stage="failed", message=error.message, error=error)
    vectors.delete_collection(project_id)
    shutil.rmtree(settings.repo_dir / project_id, ignore_errors=True)


def _run(
    project_id: str,
    repo_url: str,
    ref: str | None,
    settings: Settings,
    store: ProjectStore,
    vectors: VectorStore,
) -> None:
    owner, repo, _ = parse_repo_url(repo_url)
    repo_dir = settings.repo_dir / project_id

    store.update(project_id, status="fetching", stage="fetching", percent=0, message=f"Downloading {owner}/{repo}...")
    resolved_ref = github.fetch_repo(owner, repo, ref, repo_dir, settings)
    # Deliberately not persisted onto the record: ProjectStore.create()
    # dedupes on (repo_url, ref), and overwriting a None ref with the
    # resolved branch name would make a second identical POST /projects
    # miss the dedupe match and create a duplicate project.
    logger.info("Resolved %s/%s ref=%r to %r", owner, repo, ref, resolved_ref)
    store.update(project_id, percent=_W_FETCH)

    store.update(project_id, status="scanning", stage="scanning", message="Scanning files...")
    scan_result = scanner.scan_repo(repo_dir, settings)
    if not scan_result.files:
        raise NoIndexableFilesError("No supported source files were found after filtering.")
    store.update(
        project_id,
        files_scanned=scan_result.files_seen,
        truncated=scan_result.truncated,
        percent=_W_FETCH + _W_SCAN,
    )

    store.update(project_id, status="chunking", stage="chunking", message=f"Chunking {len(scan_result.files)} files...")
    all_chunks, line_counts, symbols_by_path = _chunk_all_files(scan_result.files)
    manifest_docs = manifest.build_manifests(scan_result.files, line_counts, symbols_by_path, scan_result.truncated)
    all_chunks += _chunk_manifest_docs(manifest_docs)
    _persist_tree_json(project_id, settings, manifest_docs.directory_tree_json)

    if not all_chunks:
        raise NoIndexableFilesError("No chunkable content was found after splitting.")
    store.update(project_id, files_indexed=len(scan_result.files), percent=_W_FETCH + _W_SCAN + _W_CHUNK)

    base_percent = _W_FETCH + _W_SCAN + _W_CHUNK
    total_chunks = len(all_chunks)
    store.update(project_id, status="embedding", stage="embedding", message=f"Embedding 0/{total_chunks} chunks...")

    def on_batch(done: int, total: int) -> None:
        fraction = done / total if total else 1.0
        percent = base_percent + int(_W_EMBED * fraction)
        store.update(
            project_id,
            percent=min(percent, 99),  # 100 is reserved for "fully stored", not "fully embedded"
            chunks_embedded=done,
            message=f"Embedding {done}/{total} chunks...",
        )

    embedder = Embedder(settings)
    texts = [c.embed_text for c in all_chunks]
    embeddings = embedder.embed_texts(texts, on_batch=on_batch)

    # Nothing is written to Chroma until every embedding has succeeded, so
    # a project can never end up half-indexed (planning/architecture.md §3.5).
    ids = [c.chunk_id() for c in all_chunks]
    documents = [c.body for c in all_chunks]
    metadatas = [c.metadata_dict(project_id) for c in all_chunks]
    vectors.delete_collection(project_id)  # guard against a stale collection from an earlier failed attempt
    vectors.add(project_id, ids, embeddings, documents, metadatas)

    store.update(
        project_id,
        status="ready",
        stage="ready",
        percent=100,
        message=f"Ready — {len(scan_result.files)} files, {total_chunks} chunks indexed.",
        chunks_embedded=total_chunks,
    )


def _chunk_all_files(files: list[ScannedFile]) -> tuple[list[Chunk], dict[str, int], dict[str, list[str]]]:
    all_chunks: list[Chunk] = []
    line_counts: dict[str, int] = {}
    symbols_by_path: dict[str, list[str]] = {}

    for f in files:
        text = read_source_text(f.abs_path)
        if text is None:
            continue
        line_counts[f.path] = text.count("\n") + 1

        chunks = chunk_text(text, path=f.path, doc_type=f.doc_type, language=f.language)
        all_chunks.extend(chunks)

        if chunks:
            unique_symbols: list[str] = []
            for c in chunks:
                for s in c.symbols:
                    if s not in unique_symbols:
                        unique_symbols.append(s)
            symbols_by_path[f.path] = unique_symbols

    return all_chunks, line_counts, symbols_by_path


def _chunk_manifest_docs(docs: ManifestDocs) -> list[Chunk]:
    synthetic_docs = [
        ("__directory_tree__", docs.directory_tree_text),
        ("__repo_summary__", docs.repo_summary_text),
        ("__file_index__", docs.file_index_text),
    ]
    chunks: list[Chunk] = []
    for path, text in synthetic_docs:
        if text.strip():
            chunks.extend(chunk_text(text, path=path, doc_type="manifest", language="text"))
    return chunks


def _persist_tree_json(project_id: str, settings: Settings, tree_json: dict) -> None:
    """Stored as a sibling of repo_dir, not inside it, so a future
    re-index can never mistake this for a scannable repo file (ADR-006:
    the (future) `directory` chat route reads this file directly instead
    of going through vector search)."""
    path: Path = settings.repo_dir / f"{project_id}.manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tree_json, indent=2), encoding="utf-8")
