# Progress Log

Update at the end of every working session. Newest entry on top.

**Current phase:** Phases 1-5 complete — full stack integrated and working end to end.
**Overall:** paste a GitHub URL into the Streamlit UI, watch it index, then ask questions and
get streamed answers with verified file:line citations. Frontend → FastAPI → LangGraph agents
→ OpenAI → Chroma, all wired and verified live.

---

## 2026-08-02 (evening) — Full-stack integration

**Done** — replaced the frontend's mock layer with real HTTP and wired everything together.

- `frontend/api_client.py` — the frontend's only network surface. One function per endpoint in
  api_contract.md, plus `ApiError` carrying `{code, message, hint}` so every backend failure
  renders as a readable message rather than a traceback. Transport failures (backend down) are
  raised in the same shape, with a hint telling the user how to start it.
- `frontend/components/sidebar.py` — real project list, add-repo → `POST /projects`, live
  indexing progress, failed-project expander with a Remove action. Progress polling uses
  `st.fragment(run_every="1.5s")` **only while something is indexing**, so the app isn't
  re-running on a timer once everything is ready.
- `frontend/components/chat.py` — consumes the real NDJSON stream: `phase` → status line,
  `sources` → citation expander (rendered before the answer finishes), `token` → incremental
  text, `revision` → discard the draft and restream (ADR-005), `final` → authoritative answer,
  `error` → inline error. Adds a small footer showing route/revisions/low-confidence, which is
  the visible evidence the agent machinery is real.
- `frontend/app.py` — per-(project, chat) conversation ids so "New chat" resets backend history
  too; auto-selects the first ready project and self-heals if it was deleted; blocking error if
  the backend is unreachable; warning banner if the backend has no OpenAI key.
- Deleted `frontend/mock_data.py` — fully superseded. Its shapes were deliberately modelled on
  api_contract.md, which is why integration was a swap rather than a rewrite.
- Root `README.md` (setup, architecture, limitations) and `run.ps1` (starts both services,
  waits for the backend before launching the UI, stops both on Ctrl+C).

**Verified live** against the running stack (real backend, real agents, real OpenAI):

| Path | Result |
|---|---|
| Frontend loads, pulls real project list | ✓ |
| Ask a question end to end | 2655-char answer, 12 sources, real `file:line` citations |
| Follow-up "Explain this function." | ✓ resolved from history across frontend→backend |
| Add repo through the UI | `octocat/Hello-World` → indexed → ready → queryable |
| Invalid URL through the UI | typed error surfaced in the sidebar, no traceback |
| "New chat" | clears transcript *and* mints a new conversation id |
| Project switching | transcripts stay separate per project |
| Backend test suite | 120 passed, still fully offline |

**Debugging note worth recording** — headless-browser screenshots of this app are unreliable,
and it cost real time to work out why. Edge's `--screenshot` fires at the *load* event and then
exits, which for a websocket-driven SPA means it captures Streamlit's loading skeleton before
the script has run. Symptoms that looked like app bugs (blank pages; backend logs showing
`GET /health` with no follow-up `GET /projects`) were the browser dying mid-script-run.
`--virtual-time-budget` helps but fast-forwards timers while real network I/O still takes real
time, so it's racy. **Use `streamlit.testing.v1.AppTest` against a running backend instead** —
it executes the real script, exercises real HTTP, and reports exceptions deterministically.

**Known gaps** (unchanged, all documented in README.md)
- Public repos only; chat history is in-memory and lost on backend restart.
- No incremental re-index / "refresh" for an already-indexed repo.
- No per-request token/cost accounting (`usage` in the `final` event is still unimplemented).
- `GET /projects/{id}/file` ("view full file" from a citation) still deferred.
- `git init` — the repo is still not under version control.

---

## 2026-08-02 (later) — LangGraph agent workflow

**Done** — implemented planning/architecture.md §4 in full (Phases 3 and 4).

- `agents/state.py` — `GraphState`, `Turn`, `RetrievedChunk`, `Citation`, and the two
  structured-output schemas (`CoordinatorPlan`, `ReviewVerdict`). Field descriptions are
  load-bearing prompt text here, not just docs — the model reads them.
- `agents/prompts.py` — every system prompt in one file.
- `agents/coordinator.py` — routes to one of 5 workflows and rewrites the question into 2-4
  self-contained search queries using conversation history. Falls back to `code_qa` with the
  raw question if the model call fails, rather than sinking the request.
- `agents/retrieval.py` — multi-query search, reciprocal-rank fusion, neighbour expansion,
  budget capping, `low_confidence` flag, and forced manifest context for architecture questions.
- `agents/explanation.py` — the streaming node, with per-route prompts; also holds the
  `directory` route's tree loader (which reads ground truth instead of searching, ADR-006).
- `agents/review.py` — grounding verdict plus **independently verified** citations: paths are
  parsed out of the draft and checked against the chunks actually retrieved, so the model's own
  claim to have cited real files is never taken at face value.
- `agents/graph.py` — 6 nodes, both conditional edges, the bounded revision loop.
- `services/conversations.py` — per-(project, conversation) history, 10-turn cap.
- `api/chat.py` — rewritten from the placeholder to drive the real graph and emit the full
  NDJSON event protocol.

**T4.0 spike finding that shaped the design:** LangGraph streams *every* node's LLM output
through the same `messages` channel — including the Coordinator's and Review's structured-output
JSON. Streaming it unfiltered would emit raw JSON fragments into the user's answer. The endpoint
therefore filters on `langgraph_node == "explanation"`, and there is a regression test
(`test_only_explanation_node_tokens_are_streamed`) pinning that behaviour.

**Open question from the last two sessions, now closed:** queried the live OpenAI account and
confirmed `gpt-4o-mini` and `text-embedding-3-small` are both valid model IDs. Left the user's
`.env` values untouched. Worth knowing: `gpt-5`, `gpt-5-mini`, and `gpt-4.1` are also available
on this account and would likely improve answer quality — a one-line `.env` change, not a code
change, thanks to ADR-010's two-tier config.

**Bug found by a unit test, not by the live run:** `_merge_sorted` re-sorted the fused results by
raw cosine score, which silently discarded the reciprocal-rank-fusion ordering that had just been
computed — making multi-query retrieval no better than single-query. Neighbours are now spliced
in around their anchor, preserving fusion order. This is precisely the class of bug that live
"looks fine to me" testing never catches, because the answers still looked plausible.

**Three issues found by the live run, not by tests** (the reverse direction — why both matter):
1. "Explain this function." as a follow-up routed to `clarify` despite correct history being
   passed. Isolating the coordinator confirmed the plumbing was fine and the *prompt* was at
   fault; rewritten with an explicit worked example.
2. The `modification` route produced 0 citations and burned both revisions, because it names
   whole files to change (`src/flask_login/login_manager.py`) without line ranges, and the
   citation regex demanded `:line`. Bare paths are now accepted.
3. An honest "this repo contains no Kubernetes code" answer was revised twice and then stamped
   with a "could not be verified" caveat — actively pressuring the next draft to invent
   something. Added `declines_for_lack_of_context` to `ReviewVerdict` so honest declines
   finalise immediately.

Also switched Chroma collections to cosine space (from the default L2) so `similarity =
1 - distance` and the relevance floor is a number that can be reasoned about rather than tuned
by trial and error.

**Testing** — 120 tests (up from 75), still fully offline and fast (~1.9s):
- `test_agents.py` (36) — routing fallbacks, rank fusion, neighbour expansion, similarity
  conversion, citation extraction/validation, the revise/finalise branch, graph compilation.
- `test_chat_stream.py` (7) — the NDJSON event contract against a fake graph.
- Fixed a test-isolation regression I introduced: after wiring the real graph, the existing
  chat endpoint test started making real OpenAI calls (suite time jumped 1.7s → 15s). Added a
  `FakeGraph` fixture; the suite is offline again.

**Live verification** against `maxcountryman/flask-login` (32 files, 183 chunks), all seven
scenarios passing with **zero wasted revisions** after the fixes above:

| Question | Route | Citations | Notes |
|---|---|---|---|
| How does authentication work? | architecture | 4 | 584 token events; streamed text == final answer exactly |
| Summarize the project architecture. | architecture | 6 | manifest docs forced into context as designed |
| What does login_user do? | code_qa | 5 | |
| *Explain this function.* (follow-up) | code_qa | 6 | resolved from history to `login_user` |
| Where to add Google Login? | modification | 3 | ordered change plan, real integration points |
| Explain the file directory. | directory | 0 | bypassed vector search, used the exact tree |
| Kubernetes operator question | architecture | 0 | correctly declined, no caveat, no invention |
| "explain this function" (no history) | clarify | 0 | asked which function |

Separately confirmed the revision loop is not dead code: fed the real reviewer a fabricated
draft ("Redis-backed token cache that rotates keys hourly") and it correctly rejected it with
specific issues and useful recovery queries.

**Known gaps**
- Streamlit frontend (Phase 5) — not started. This is the remaining major deliverable.
- `usage` (token/cost accounting) in the `final` event — specified in api_contract.md, not
  implemented; the contract now says so explicitly.
- `GET /projects/{id}/file` — still deferred (Phase 5 concern, for citation "view full file").
- `git init` — the repo is still not under version control.
- An indexed `flask-login` project is left in `data/` from verification; delete it via
  `DELETE /projects/{id}` if a clean slate is wanted.

**Next**
- Streamlit frontend (Phase 5), against a backend that is now fully functional.

---

## 2026-08-02 — RAG indexing pipeline

**Done** — implemented planning/architecture.md §3 in full (Phases 1 and 2 of
implementation_plan.md), per explicit instruction to build the indexing pipeline next.

- `ingestion/github.py` — real GitHub API integration: repo metadata/existence check, streaming
  zipball download with a size cap, safe extraction (rejects path traversal and symlink zip
  entries outright, strips the `owner-repo-sha/` prefix). Rate-limit and not-found responses
  map to the typed errors from Phase 0.
- `ingestion/scanner.py` — recursive walk with the directory denylist, a doc/code/config
  **whitelist** (safer than a binary blacklist — nothing unrecognized gets read), NUL-byte
  binary sniff, lockfile/generated-file filters, `MAX_FILES` truncation with a doc-first/
  shallow-first/larger-first ranking heuristic.
- `ingestion/chunker.py` — language-aware splitting via `langchain-text-splitters`
  (`Language` enum members verified against the installed 1.1.2, not assumed — several
  languages we index, e.g. shell/sql/json/yaml, have no LangChain mapping and correctly fall
  back to the plain recursive splitter), exact line-number attribution, crude regex symbol
  extraction, and the synthetic embedding header (embedded text ≠ stored text, ADR-004).
- `ingestion/manifest.py` — `__directory_tree__` (JSON + rendered text), `__repo_summary__`
  (language mix, entry points, dependency manifests, CI/Docker/test signals, README excerpt),
  `__file_index__` — all embedded like real documents so retrieval can surface them later.
- `vectorstore/embedder.py` — batched OpenAI embeddings (96/request), tenacity retry on
  rate-limit/connection/5xx errors, auth errors fail fast without retrying. API surface
  verified against installed openai==2.52.0.
- `vectorstore/store.py` — Chroma `PersistentClient` wrapper, one collection per project,
  verified against installed chromadb==1.5.9 (confirmed `NotFoundError` behavior, metadata
  constraints — empty dicts are rejected — and the `embedding_function=None` pattern needed
  since we always supply precomputed vectors).
- `ingestion/pipeline.py` — orchestrates the whole thing with weighted progress (10/10/15/65)
  reported through the existing `ProjectStore`; nothing reaches Chroma until every chunk is
  embedded, so a project can never end up half-indexed; failure cleans up the collection and
  extracted repo dir.
- `POST /projects` now schedules `run_indexing` via `BackgroundTasks`, but only when a new
  record was actually created (not on a dedupe hit). `DELETE /projects/{id}` now also removes
  the Chroma collection, extracted repo, and manifest JSON, not just the registry entry.
- `ProjectStore` gained real disk persistence — `data/projects.json`, atomic writes via
  `os.replace`, loaded on construction — closing the gap flagged in the last session.

**Bug caught and fixed during live verification** (this is exactly why the live check matters,
not just the mocked test suite): `Settings.chroma_dir` / `repo_dir` / `projects_file` were
defined as project-root-relative `Path` defaults, but `.env`'s `REPO_DIR=./data/repos`-style
values (copied from `.env.example`) are relative *strings* that pydantic-settings resolves
against the process's cwd at the point of use — overriding the smart default and silently
writing everything to `backend/data/` when uvicorn was launched from `backend/`. Fixed with a
`field_validator` on those three fields that resolves any relative override against the
project root, so behavior is now identical regardless of launch directory. Caught only because
the live run inspected the actual filesystem rather than trusting the API's 200 OK.

**Also caught, not a bug:** deleting a Chroma collection removes it from the app's view (count
goes to 0, further queries correctly 404-equivalent) but can leave orphaned HNSW segment files
under `data/chroma/<uuid>/` on disk — this looks like a chromadb 1.5.9 internal segment-GC
characteristic, not something the public `delete_collection` API exposes a hook for. Not
pursued further since the application-level contract (deleted data is inaccessible and doesn't
leak into other collections) holds; worth knowing about if `data/chroma` disk usage ever needs
auditing.

**Testing** — 75 pytest tests total (up from 26), all offline/fast (~1.7s):
- `test_scanner.py`, `test_chunker.py` (the critical line-reconstruction test lives here),
  `test_manifest.py` — pure logic against a fixture repo, zero network/API calls.
- `test_github.py` — respx-mocked GitHub API, plus real-zipfile-based tests of `safe_extract`'s
  path-traversal and symlink rejection (security-critical code, not just informally trusted).
- `test_embedder.py` — mocked OpenAI client; retry-then-succeed and retry-exhausted paths run
  instantly by patching `time.sleep` under tenacity rather than actually backing off.
- `test_vector_store.py` — real Chroma against temp directories (no mocking needed; it's local).
- `test_pipeline.py` — full integration with only the network boundary (`fetch_repo`) and paid
  boundary (`Embedder`) faked; everything else (scan/chunk/manifest/store/progress) is real.
- `test_project_store.py` — persistence round-trips through fresh `ProjectStore` instances.
- Fixed a pre-existing test-isolation gap along the way: `conftest.py`'s `client` fixture now
  stubs `run_indexing` for API-level tests, since `TestClient` runs `BackgroundTasks`
  synchronously inside `client.post()` — without the stub, every API test that created a
  project would have fired a real GitHub call.

**Live verification** (real GitHub + real OpenAI, `octocat/Hello-World` — negligible cost,
tiny repo, `text-embedding-3-small`): full `queued → fetching → embedding(35%→100%) → ready`
progression observed live over the polling endpoint; 1 file scanned, 4 chunks embedded (1 real
+ 3 manifest docs); a fresh real embedding call + `vector_store.search()` correctly ranked all
4 stored chunks by relevance; dedupe on repost confirmed (same `project_id`, no re-index);
`DELETE` confirmed to remove the registry entry, Chroma collection, extracted files, and
manifest JSON; invalid-URL and real-nonexistent-repo (real GitHub 404) error paths both
verified against the live server.

**Known gaps, deliberately out of scope for this session**
- LangGraph agents (Coordinator/Retrieval/Explanation/Review) — Phase 3/4, not started.
  `vector_store.search()` works (proven live above) but nothing calls it yet.
- `GET /projects/{id}/file` — deferred; now feasible since files persist, but it's a
  chat-citation display concern, not indexing.
- `?force=true` re-index — still deferred, nothing to force yet without agents consuming it.
- `git init` — still not done; repo remains outside version control.
- Streamlit frontend — not started (Phase 5).

**Next**
- LangGraph agent graph (Phase 3), or the Streamlit frontend against the now-real indexing
  pipeline (Phase 5) — whichever the user wants next.

---

## 2026-08-01 — FastAPI endpoint scaffold

**Done** — built ahead of the phase order on explicit instruction: FastAPI endpoints first,
verified reachable, before any RAG/indexing or LangGraph work.

- `backend/app/config.py` — `pydantic-settings` `Settings`, `.env` resolved relative to the
  project root regardless of process cwd (via `Path(__file__).resolve().parents[2]`), so
  `uvicorn --app-dir backend` and `pytest` from either directory both pick up the same file.
- `backend/app/errors.py` — `AppError` hierarchy (9 typed subclasses matching architecture §8)
  with a single FastAPI handler matched via MRO, plus a catch-all `Exception` handler that logs
  a correlation id server-side and returns only that id to the client — no tracebacks leak.
- `backend/app/schemas.py` — pydantic models for every response/request shape in api_contract.md
  that's implemented so far.
- `backend/app/services/repo_url.py` — real `parse_repo_url()` (regex-based), accepts full URL,
  `/tree/{ref}`, `.git` suffix, bare `owner/repo`, case-insensitive host; rejects non-GitHub
  hosts and non-repo-root paths (e.g. a PR URL). This is genuine validation logic, not a stub.
- `backend/app/services/project_store.py` — thread-safe in-memory `ProjectStore` (create/list/
  get/update/delete), with dedupe on `(repo_url, ref)` and idempotent delete. Explicitly *not*
  disk-backed yet (ADR-008 persistence is Phase 2 T3.7) — projects don't survive a restart.
- `backend/app/api/health.py`, `projects.py`, `chat.py` — `GET /health` does a real Chroma
  `PersistentClient` open (not faked) and reports `openai_configured` from settings;
  `POST/GET/DELETE /projects` are fully real CRUD against the in-memory store; `POST /chat`
  has a real 404/409 readiness gate but streams a canned placeholder NDJSON body for `ready`
  projects (nothing can reach `ready` yet since the pipeline doesn't exist) — clearly marked
  `TODO(Phase 3/4)` at the point it needs to become the LangGraph graph's output stream.
- `backend/tests/` — 26 pytest cases (endpoints + repo_url parsing edge cases), all passing.
- **Live reachability check**, per the explicit ask: started uvicorn for real, hit all 11
  endpoint/status combinations over actual HTTP (not just TestClient) — health, docs, create
  (valid + invalid URL), list, get (found + 404), chat (409 not-ready + 404 unknown), delete,
  and confirmed post-delete 404. All 11 passed. Server was then stopped.
- Root `.gitignore` and `.env.example` added (T1.1's `.gitignore` half, and T1.4).

**Not done, deliberately** (per instruction not to build these yet): GitHub fetch/scan/chunk/
embed pipeline, ChromaDB wiring for real storage, LangGraph agents, streaming from a real LLM.
`/chat`'s streamed answer and `/health`'s `openai_configured` are the only places OpenAI is
even referenced, and neither calls the API.

**Known gaps to close in Phase 2 proper**
- Project registry is in-memory only — add `data/projects.json` with atomic writes (T3.7),
  then re-verify T3.13 (restart survival).
- `git init` (T1.1) still not done — repo remains outside version control.
- `?force=true` reindex flag deferred — nothing to force yet.
- `GET /projects/{id}/file` deferred — depends on extracted repo files existing.

**Next**
- Resolve the OpenAI model ID open question (below), then Phase 1 ingestion (T2.1–T2.19).

---

## 2026-08-01 — Planning

**Done**
- Surveyed the working tree: `backend/`, `frontend/`, `planning/` all empty; `.env` and
  `requirements.txt` both 0 bytes; `venv/` exists on Python 3.14.6. **Not a git repo yet.**
- Verified dependency availability against the actual venv rather than assuming, because
  Python 3.14 is new enough to plausibly lack native wheels:

  | Package | Resolved | Wheel |
  |---|---|---|
  | faiss-cpu | 1.14.3 | `cp314-cp314-win_amd64` |
  | chromadb | 1.5.9 | `cp39-abi3-win_amd64` |
  | langgraph | 1.2.10 | pure Python |
  | langchain-openai | 1.4.1 | pure Python |
  | fastapi | 0.141.1 | pure Python |
  | streamlit | 1.60.0 | pure Python |
  | tree-sitter | 0.26.0 | `cp314-cp314-win_amd64` |
  | tree-sitter-language-pack | 1.13.7 | `cp310-abi3-win_amd64` |

  No blocker on 3.14 — the venv stays (ADR-011).
- Wrote the full planning set: README, architecture, decisions (11 ADRs), api_contract,
  implementation_plan (8 phases), task_breakdown (~90 tasks).

**Decisions locked**
- ChromaDB over FAISS (ADR-001) — metadata storage and per-project isolation matter more here
  than ANN speed.
- Zipball download over `git clone` (ADR-002) — no `git` binary dependency, enforceable size cap.
- Stream the draft, then revise visibly (ADR-005) — resolves the streaming ↔ review conflict.
- The `directory` question bypasses vector search (ADR-006) — we have ground truth at index
  time; don't launder it through an approximate retriever.
- NDJSON over SSE (ADR-007).

**Open questions**
1. **OpenAI model IDs** — defaults are intentionally unwritten in `config.py`. Confirm current
   chat model IDs before Phase 0 T1.5. Embeddings settled: `text-embedding-3-small`.
2. **LangGraph 1.x API surface** — 1.2.10 and langchain-openai 1.4.1 are both post-1.0.
   Structured output, conditional-edge signatures, and stream modes shifted across that
   boundary. T4.0 spikes a 2-node graph against the *installed source* before the real graph is
   built. Largest schedule risk in the plan.
3. **Demo repo** — needs to be small enough to index fast, but have real auth code so target
   questions 1 and 4 have something to find. Candidates: `psf/requests` (small, no auth),
   a FastAPI OAuth example repo (has auth, tiny). Pick during Phase 2.

**Next**
- Phase 0 in order: T1.1 → T1.8. Resolve open question 1 before T1.5.

---

## Template

```
## YYYY-MM-DD — <phase>

**Done**
- <task id> — <what landed>

**Blocked**
- <task id> — <blocker, and what would unblock it>

**Decisions**
- <new ADR number and one-line summary, if any>

**Next**
- <task ids>
```
