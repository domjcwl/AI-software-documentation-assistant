# Implementation Plan

Eight phases. Each has an **exit criterion** that is demonstrable, not a feeling. Do not start
a phase until its predecessor's exit criterion passes — the phase order is chosen so that every
phase is verifiable in isolation, which is what keeps debugging cheap later.

Rough effort: ~3 focused days. Phases 1–4 are the load-bearing half.

---

## Phase 0 — Project skeleton and config

- `git init` (the working tree is **not** currently a git repo) and add `.gitignore` covering
  `venv/`, `data/`, `.env`, `__pycache__/`, `*.pyc`, `.pytest_cache/`.
- Fill the empty `requirements.txt`. Pin the versions verified against this venv:
  `fastapi==0.141.1`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `httpx`,
  `python-dotenv`, `chromadb==1.5.9`, `langgraph==1.2.10`, `langchain-openai==1.4.1`,
  `langchain-text-splitters`, `openai`, `streamlit==1.60.0`, `tenacity`, `pytest`,
  `pytest-asyncio`, `respx`.
- `.env.example` with every key from architecture §7; real `.env` stays empty in git.
- `backend/app/config.py` — `pydantic-settings` `Settings`, cached accessor.
  **Confirm current OpenAI chat model IDs before writing the defaults.**
- `backend/app/errors.py` — `AppError` base + the subclasses in architecture §8, plus a FastAPI
  exception handler producing the standard error body.

**Exit:** `pip install -r requirements.txt` completes clean; `python -c "from app.config import get_settings; print(get_settings())"` prints resolved settings.

---

## Phase 1 — Ingestion, no embeddings

Build and verify the whole file pipeline before spending a cent on the OpenAI API. Every piece
here is deterministic and unit-testable, which is precisely why it goes first.

1. `ingestion/github.py` — URL parsing (all four accepted forms), zipball download with byte
   cap, safe extraction (reject `..`, absolute paths, symlinks), strip the
   `{owner}-{repo}-{sha}/` prefix.
2. `ingestion/scanner.py` — recursive walk, directory denylist, binary sniff, lockfile and
   size filters, `doc_type` classification, `MAX_FILES` cap with the ranking heuristic.
3. `ingestion/chunker.py` — language mapping, `from_language()` splitters with fallback,
   `add_start_index=True`, newline-count → `start_line`/`end_line`, regex symbol extraction,
   synthetic embedding header.
4. `ingestion/manifest.py` — `__directory_tree__`, `__repo_summary__`, `__file_index__`, plus
   the persisted tree JSON for the `directory` route.

Tests: a fixture mini-repo under `backend/tests/fixtures/` containing a `node_modules/` dir, a
binary file, a lockfile, an oversized file, and two real source files. Assert exactly the right
files survive. Assert that for a known chunk, `source.splitlines()[start_line-1:end_line]`
reconstructs the chunk body — this is the citation-correctness test and it must be exact.

**Exit:** a CLI scratch script indexes a real small repo (suggest `psf/requests`) and prints
file count, chunk count, and 3 sample chunks with correct line ranges. Zero API calls made.

---

## Phase 2 — Vector store + FastAPI endpoints

1. `vectorstore/embedder.py` — batched OpenAI embeddings (96/request), tenacity backoff on
   429/5xx, typed failure.
2. `vectorstore/store.py` — Chroma `PersistentClient`, per-project collections, `add` /
   `search` (with `where` metadata filters) / `delete` / `count`.
3. `services/jobs.py` — job registry, stage transitions, weighted percent (10/10/15/65).
4. `ingestion/pipeline.py` — wires Phase 1 + embeddings, emits progress at every stage,
   guarantees no partially-embedded project reaches `ready`.
5. `api/health.py`, `api/projects.py` — all five endpoints from
   [api_contract.md](api_contract.md) except `/chat`.
6. `data/projects.json` registry with atomic writes.

**Exit:** `POST /projects` on a real repo, then poll `GET /projects/{id}` and watch it walk
`queued → … → ready` with a rising percent. `GET /projects` lists it. `DELETE` removes vectors
*and* files. Restart uvicorn — the project is still there and still queryable.

---

## Phase 3 — LangGraph agents (non-streaming)

Get the graph *correct* first; add streaming after. Debugging a streaming graph that returns
wrong answers is two problems at once.

1. `agents/state.py`, `agents/prompts.py` (all prompts in one file, none inline).
2. `coordinator.py` — structured output: `route`, `search_queries`, `doc_type_filter`,
   `needs_clarification`. History-aware query rewriting is the critical part; a follow-up like
   "explain this function" must become a self-contained query.
3. `retrieval.py` — multi-query, reciprocal-rank fusion dedupe, neighbor expansion (±1 chunk on
   the top 3), token cap, `low_confidence` flag.
4. `explanation.py` — route-specific prompts, inline `path:start-end` citation format.
5. `review.py` — `ReviewVerdict` structured output, citation-path validation against the real
   file list, `revise` vs `finalize`, revision ceiling of 2.
6. `graph.py` — nodes, conditional edges from Coordinator and from Review, `compile()`.

> Verify the LangGraph 1.x and langchain-openai 1.x APIs against the installed packages —
> structured output, conditional-edge signatures, and stream modes all shifted across the 1.0
> boundary. Read the installed source; don't port patterns from older tutorials.

**Exit:** a pytest that runs all five target questions against an indexed repo and asserts, for
each: correct route, ≥1 citation, and every cited path exists on disk. Plus one adversarial
question about a subsystem the repo doesn't have — the answer must decline rather than invent.

---

## Phase 4 — Streaming `/chat`

1. Async generator yielding the NDJSON event types in [api_contract.md](api_contract.md), with
   the ordering guarantees enforced.
2. Bridge LangGraph's stream to `phase` / `route` / `sources` / `token` events; only the
   Explanation node emits `token`.
3. `revision` event on the review→retrieval loop.
4. `StreamingResponse(media_type="application/x-ndjson")`; disable buffering; `error` line +
   clean close on mid-stream failure.
5. `services/conversations.py` — history capped at 10 turns, fed to the Coordinator.

**Exit:** `curl -N -X POST localhost:8000/chat -d '...'` shows tokens arriving incrementally
(not one burst at the end), correct event ordering, and exactly one terminal line.

---

## Phase 5 — Streamlit frontend

1. `api_client.py` — typed methods; `stream_chat()` yields decoded event dicts via
   `iter_lines()`. Frontend contains no business logic.
2. `components/sidebar.py` — repo URL input, add button, project list with status pills,
   delete, and the polling progress bar (`st.progress` + stage message, ~1.5 s cadence).
3. `components/chat.py` — `st.chat_message` transcript, `st.chat_input`, streaming into a
   placeholder container, phase indicator, and **draft discard on `revision`** (ADR-005).
4. `components/citations.py` — `st.expander("📄 Sources (n)")` with per-source path, line range,
   syntax-highlighted snippet, and a "view full file" call to `/projects/{id}/file`.
5. `state.py` — per-project conversation history and `conversation_id` in `st.session_state`,
   so switching projects switches transcripts without losing either.
6. Error rendering: `st.error` with `message` + `hint`, never a traceback.

**Exit:** full manual pass — add a repo, watch the bar fill, ask all five target questions,
expand citations, switch to a second project and confirm its history is separate, then switch
back and confirm the first transcript is intact.

---

## Phase 6 — Hardening

- Cost guardrails: token-count context before every call; log per-request usage.
- Concurrency: two simultaneous index jobs must not corrupt `projects.json` (async lock).
- Empty/near-empty repos, repos with one language, repos with no README.
- Cancel/cleanup: failed job leaves no orphan collection or temp directory.
- `MAX_FILES` truncation warning surfaced in the UI.
- Structured logging with a correlation id echoed in `internal_error` responses.

**Exit:** the adversarial list in [task_breakdown.md](task_breakdown.md) §7 passes end to end.

---

## Phase 7 — Documentation and demo

- Root `README.md`: setup, `.env` keys, two run commands, architecture diagram, screenshots,
  known limitations (lifted verbatim from architecture §9 and ADR-008).
- `planning/progress.md` finalized.
- Rehearsed demo script: index a mid-size repo, ask one question per route, deliberately trigger
  one error to show the handling.

**Exit:** a clean clone + `pip install` + two commands reaches a working app with no undocumented
steps.

---

## Stretch (only if Phases 0–7 are done)

1. tree-sitter AST chunking behind a config flag (ADR-003).
2. Cross-encoder reranking of retrieved chunks.
3. SQLite `ConversationStore` (ADR-008) for restart-durable history.
4. Mermaid architecture diagram generated from the module graph.
5. Per-project answer-cache on question hash.

---

## Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| LangGraph/langchain 1.x API differs from prior patterns | Phase 3 stalls | Read installed package source first; spike the graph on a trivial 2-node example before wiring 4 agents |
| Embedding cost on a large repo | Budget | `MAX_FILES` + `MAX_FILE_BYTES` caps; use `text-embedding-3-small`; test on small repos |
| Streaming + revision UX confuses users | Poor demo | Explicit "Refining answer…" copy; cap at 2; phase indicator throughout |
| Poor retrieval on huge monorepos | Weak answers | Multi-query + RRF + neighbor expansion; `low_confidence` honest fallback |
| Streamlit rerun model fights streaming | Flicker/loss | Keep transcript in `session_state`; stream into one placeholder; replace buffer with `final.answer` |
| Citation line numbers drift | Breaks core promise | Exact reconstruction test in Phase 1; path validation in Review |
