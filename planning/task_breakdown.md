# Task Breakdown

Checklist form. `[ ]` open · `[~]` in progress · `[x]` done. Mirror status changes into
[progress.md](progress.md). `→` marks a hard dependency.

---

## 1. Setup (Phase 0)

- [ ] T1.1 `git init` (repo is still not a git repository — pending)
- [x] T1.1b `.gitignore` (`venv/ data/ .env __pycache__/ *.pyc .pytest_cache/`)
- [x] T1.2 Fill `requirements.txt` with the pins from implementation_plan Phase 0
- [x] T1.3 `pip install -r requirements.txt` in the existing 3.14.6 venv — installed clean, all imports verified
- [x] T1.4 `.env.example` with all keys from architecture §7
- [~] T1.5 `backend/app/config.py` — written with placeholder model IDs (`gpt-4o-mini`); **OpenAI chat model IDs still need confirming before Phase 3**
- [x] T1.6 `backend/app/errors.py` — `AppError` hierarchy + FastAPI handler (with correlation-id logging on unhandled exceptions)
- [x] T1.7 `backend/app/schemas.py` — pydantic models for every shape in api_contract.md implemented so far
- [x] T1.8 `backend/app/main.py` — app factory, CORS for `localhost:8501`, router mounts

## 2. Ingestion (Phase 1) → T1.5 — done (2026-08-02)

- [x] T2.1 `github.py::parse_repo_url` — lives in `services/repo_url.py` (Phase 0), reused as-is by `github.py`
- [x] T2.2 `github.py::download_zipball` — streaming, byte cap, 404/403(rate-limit) mapped to error codes
- [x] T2.3 `github.py::safe_extract` — rejects `..` and absolute paths, rejects symlink entries, strips the `owner-repo-sha/` prefix
- [x] T2.4 `scanner.py` — walk + directory denylist
- [x] T2.5 `scanner.py` — binary sniff (NUL in first 8 KB); no separate extension denylist — a doc/code/config **whitelist** is used instead (see scanner.py module docstring for why that subsumes it)
- [x] T2.6 `scanner.py` — lockfile/generated/minified filters + oversized filter
- [x] T2.7 `scanner.py` — `doc_type` classification table
- [x] T2.8 `scanner.py` — `MAX_FILES` cap + ranking heuristic + `truncated` flag
- [x] T2.9 `chunker.py` — extension → LangChain `Language` map (verified against installed langchain-text-splitters==1.1.2), `from_language()` + fallback
- [x] T2.10 `chunker.py` — `add_start_index` → `start_line`/`end_line` conversion
- [x] T2.11 `chunker.py` — regex symbol extraction (language-agnostic, applied uniformly — see ADR-004)
- [x] T2.12 `chunker.py` — synthetic embedding header (embedded text ≠ stored text, ADR-004)
- [x] T2.13 `manifest.py` — `__directory_tree__` text + JSON; persisted separately by `pipeline.py` as `{project_id}.manifest.json` (ADR-006)
- [x] T2.14 `manifest.py` — `__repo_summary__` (languages, entry points, deps, CI, tests, README head)
- [x] T2.15 `manifest.py` — `__file_index__`, with per-file symbols rolled up from chunking (no extra file read)
- [x] T2.16 Fixture mini-repo at `backend/tests/fixtures/mini_repo/`
- [x] T2.17 **Test:** scanner keeps exactly the expected files (`test_scanner.py`)
- [x] T2.18 **Test (critical):** line-reconstruction test passes exactly, verified against the real installed splitter, not assumed (`test_chunker.py::test_chunk_text_line_numbers_reconstruct_exactly`)
- [x] T2.19 Real repo verified live through the running API (`octocat/Hello-World`) — see progress.md; a standalone zero-API-call scratch script was not written since the live path covers the same ground with less duplication

## 3. Vector store & API (Phase 2) → §2 — done (2026-08-02)

- [x] T3.1 `embedder.py` — batching at 96, tenacity backoff (verified against installed openai==2.52.0), typed failures
- [x] T3.2 `store.py` — Chroma `PersistentClient`, per-project collection naming (verified against installed chromadb==1.5.9)
- [x] T3.3 `store.py` — `add` / `search` (with `where` filter) / `delete_collection` / `count`
- [x] T3.4 Folded into `ProjectStore` rather than a separate `services/jobs.py` — the record already carries `stage`/`percent`/`message`, and a second parallel registry would just be state to keep in sync for no benefit at this scale (deliberate simplification vs. architecture.md's original two-registry sketch)
- [x] T3.5 `pipeline.py` — full orchestration (fetch → scan → chunk+manifest → embed → store) + weighted progress emission
- [x] T3.6 `pipeline.py` — nothing is written to Chroma until every embedding succeeds; failure path deletes any collection + extracted repo dir
- [x] T3.7 `data/projects.json` registry, atomic write (`os.replace`), loaded on `ProjectStore` construction
- [x] T3.8 `GET /health` — real Chroma-open check (via the shared singleton, not a throwaway client) + `openai_configured` from settings
- [~] T3.9 `POST /projects` (202, dedupe existing, now wired to `BackgroundTasks.add_task(run_indexing, ...)`) — dedupe verified live; `?force=true` re-index still deferred
- [x] T3.10 `GET /projects` and `GET /projects/{id}`
- [x] T3.11 `DELETE /projects/{id}` — now removes the Chroma collection, extracted repo dir, and manifest JSON too, not just the registry entry; verified idempotent
- [ ] T3.12 `GET /projects/{id}/file` with traversal guard — still deferred; feasible now that files persist, but it's a citation/chat-display concern (Phase 5), not indexing
- [x] T3.13 **Verify:** persistence tested directly (`test_project_store.py`: create/update/delete all survive a fresh `ProjectStore` instance pointed at the same file) rather than a live process restart, which is more reliable to assert against automatically

**Also landed, not originally itemized:**
- [x] `POST /chat` scaffolded early (Phase 0): real project-lookup + readiness gate (404/409), placeholder
  NDJSON stream matching the api_contract wire format for `ready` projects, marked `TODO(Phase 3/4)`
- [x] **Bug caught and fixed during live verification:** `chroma_dir`/`repo_dir`/`projects_file` defaults
  were written as project-root-relative `Path`s, but `.env`'s `REPO_DIR=./data/repos`-style values
  overrode them with *cwd*-relative strings — indexing from `backend/` silently wrote to
  `backend/data/` instead of the real `data/`. Fixed with a `field_validator` that resolves any
  relative override against the project root. See `config.py`.
- [x] 26 pytest tests (`backend/tests/`) covering all of the above, including URL-parsing edge cases
- [x] Live reachability check: server started for real, all 11 endpoint/status-code combinations
  hit over actual HTTP and verified (see progress.md 2026-08-01 entry)

## 4. Agents (Phase 3) → §3

- [x] T4.0 Spiked LangGraph 1.x on a 2-node graph. **Key finding:** structured-output calls
  stream their raw JSON through the same `messages` channel as the explanation prose, so the
  API layer must filter on `langgraph_node` — unfiltered, coordinator JSON leaks into the
  user's answer. Also confirmed `astream(stream_mode=["updates","messages"])` and
  `with_structured_output()` both work as needed.
- [x] T4.1 `state.py` — `GraphState`, `Turn`, `RetrievedChunk`, `Citation`, `CoordinatorPlan`, `ReviewVerdict`
- [x] T4.2 `prompts.py` — all system prompts, none inline elsewhere
- [x] T4.3 `coordinator.py` — route classification (5 routes), with a code_qa fallback if the model call fails
- [x] T4.4 `coordinator.py` — history-aware query rewriting; verified live that "Explain this
  function." resolves against the prior turn instead of asking for clarification
- [x] T4.5 `coordinator.py` — clarify path (first-message "explain this function" with no referent)
- [x] T4.6 `retrieval.py` — multi-query search + reciprocal-rank fusion
- [x] T4.7 `retrieval.py` — neighbour expansion (±1 chunk on top 3), spliced around the anchor
- [x] T4.8 `retrieval.py` — chunk/char budget + `low_confidence` flag (cosine floor 0.20)
- [x] T4.9 `retrieval.py` — `architecture` route forces `__repo_summary__`/`__file_index__` into context
- [x] T4.10 `explanation.py` — per-route prompts + inline `path:start-end` citations
- [x] T4.11 `explanation.py` — `directory` route consumes the persisted tree JSON, not search results
- [x] T4.12 `explanation.py` — `modification` route emits an ordered change plan
- [x] T4.13 `review.py` — grounding verdict + citation paths parsed and validated against the
  chunks actually retrieved (the model's own "yes it cited real files" claim is overridden)
- [x] T4.14 `review.py` — revise/finalise branch, ceiling of 2, caveat at the ceiling
- [x] T4.15 `graph.py` — 6 nodes, both conditional edges, `compile()`
- [x] T4.16 **Test:** all five target questions verified live against `maxcountryman/flask-login`
  — correct route each time, real citations, 0 revisions after tuning (see progress.md)
- [x] T4.17 **Test:** absent-subsystem question declines honestly instead of inventing; verified
  live and covered by a unit test

**Deviations from the original §4 design, all deliberate:**
- `ReviewVerdict` gained a `declines_for_lack_of_context` field. Without it, an answer correctly
  saying "this repo has no Kubernetes code" was treated as uncited, revised twice, and then
  stamped with a "could not be verified" caveat — actively pressuring the next draft to invent
  something. Honest declines now finalise immediately.
- Citations accept a bare `path` as well as `path:start-end`, attributed to the retrieved span.
  The `modification` route names whole files to change without line ranges, and rejecting those
  caused pointless revision loops.
- The `directory` route is exempt from the citation requirement alongside `clarify`: its context
  is the complete file tree, not a retrieved chunk set, so there is nothing to validate against.
- Chroma collections are now created with cosine space rather than the default L2, so
  `similarity = 1 - distance` and the relevance floor is a number that can be reasoned about
  rather than tuned blindly.

## 5. Streaming (Phase 4) → §4 — done (2026-08-02)

- [x] T5.1 NDJSON event serializer; ordering guarantees covered by `test_chat_stream.py`
- [x] T5.2 Bridge LangGraph stream → `phase` / `route` / `sources` / `token`, filtered to the explanation node
- [x] T5.3 `revision` event on the review→retrieval loop
- [x] T5.4 `POST /chat` `StreamingResponse` with `application/x-ndjson`
- [x] T5.5 Mid-stream failure → `error` line + clean close
- [x] T5.6 `services/conversations.py` — 10-turn cap, fed to the Coordinator, cleared on project delete
- [x] T5.7 **Verify:** live run streamed 584 token events for one answer, exactly one terminal
  line, and the concatenated tokens matched `final.answer` exactly
- [ ] T5.8 Per-request token/cost accounting (`usage` in the `final` event) — **not implemented**

## 6. Frontend (Phase 5) → §5 — done (2026-08-02)

- [x] T6.1 `api_client.py` — typed methods + `stream_chat()` NDJSON decoder + `ApiError`
- [x] T6.2 `sidebar.py` — repo input, add, validation feedback (errors surfaced from the backend)
- [x] T6.3 `sidebar.py` — project list, select, remove-failed; status shown as a live progress bar
- [x] T6.4 `sidebar.py` — polling progress bar via `st.fragment(run_every="1.5s")`, active only
  while something is indexing
- [x] T6.5 `chat.py` — transcript via `st.chat_message` + `st.chat_input`
- [x] T6.6 `chat.py` — stream into a placeholder container
- [x] T6.7 `chat.py` — phase indicator ("Searching the codebase…", "Checking the answer…")
- [x] T6.8 `chat.py` — **discard draft on `revision`** and stream the replacement
- [x] T6.9 `chat.py` — replace token buffer with `final.answer` on completion
- [x] T6.10 Citations — expander with path, line range, similarity, highlighted snippet.
  Folded into `chat.py` rather than a separate `citations.py`: it is ~25 lines used only by
  the transcript, and a module boundary there would have been ceremony, not structure.
- [ ] T6.11 "View full file" via `/projects/{id}/file` — still deferred (backend endpoint T3.12
  is also still open); the snippet in the expander covers the common case
- [x] T6.12 Per-project transcripts + `conversation_id`, preserved across switches (in `app.py`;
  no separate `state.py` — `init_state()` is a dozen lines)
- [x] T6.13 Error rendering — `message` + `hint`, no tracebacks, incl. backend-unreachable
- [x] T6.14 Missing-API-key banner from `/health`
- [ ] T6.15 `truncated: true` coverage warning — backend reports it, UI does not surface it yet

## 7. Hardening (Phase 6) — adversarial checklist

- [ ] T7.1 Invalid URL / non-GitHub URL / typo'd owner
- [ ] T7.2 Private repo (404) and deleted repo
- [ ] T7.3 GitHub rate limit without `GITHUB_TOKEN`
- [ ] T7.4 Repo over the size cap
- [ ] T7.5 Repo with zero indexable files (e.g. binary-only)
- [ ] T7.6 Repo with no README (`__repo_summary__` must still build)
- [ ] T7.7 Chat before indexing finishes → `project_not_ready` with percent
- [ ] T7.8 Invalid `OPENAI_API_KEY` → clear `openai_auth` message
- [ ] T7.9 OpenAI 429 mid-stream → `error` line, transcript not corrupted
- [ ] T7.10 Two concurrent index jobs → `projects.json` intact (async lock)
- [ ] T7.11 Delete a project mid-chat → clean error, no crash
- [ ] T7.12 Failed job leaves no orphan collection or temp dir
- [ ] T7.13 Question in a language the repo doesn't use → honest "not found"
- [ ] T7.14 Very long question / very long file → token caps hold
- [ ] T7.15 Per-request token usage logged

## 8. Docs & demo (Phase 7)

- [ ] T8.1 Root `README.md` — setup, env, run commands, diagram, limitations
- [ ] T8.2 Screenshots: indexing progress, streaming answer, expanded citations
- [ ] T8.3 Finalize `planning/progress.md`
- [ ] T8.4 Rehearse demo: one question per route + one deliberate error
- [ ] T8.5 Clean-clone verification on a fresh venv

---

## Acceptance criteria — the five target questions

Each must produce a correct route, ≥1 valid citation, and no fabricated paths.

| # | Question | Route | Must demonstrate |
|---|---|---|---|
| 1 | How does authentication work? | `code_qa` | Multi-query retrieval finds auth code even when the word "authentication" never appears in it |
| 2 | Summarize the project architecture. | `architecture` | Uses `__repo_summary__` + `__file_index__`, not just 12 random code chunks |
| 3 | Explain this function. | `code_qa` (or `clarify`) | Resolves the referent from conversation history; asks for clarification when there is none |
| 4 | Where should I modify the code to add Google Login? | `modification` | Two-pass retrieval (target subsystem + analogous existing feature); ordered change plan with integration points |
| 5 | Explain the project's file directory. | `directory` | Bypasses vector search entirely (ADR-006); tree is exact, elisions marked |
