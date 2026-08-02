# Architecture Decision Records

Format: context → decision → consequences. Superseded ADRs stay in the file, marked.

---

## ADR-001 — ChromaDB over FAISS
**Status:** Accepted

**Context.** The brief allows either. Both are viable; the differences only appear once you
account for *multi-project* support and citations.

**Decision.** ChromaDB, `PersistentClient`, one collection per project (`proj_{project_id}`).

**Why.** FAISS is a pure index of vectors — it stores no metadata and no persistence of its own.
Using it here would require hand-building three things Chroma gives for free: (1) an
`int id → {path, start_line, end_line, doc_type}` sidecar store, since every citation depends on
that metadata; (2) a save/load protocol per project; (3) metadata filtering, needed by the
`architecture` route to bias toward `doc`/`manifest` chunks. That's meaningful custom code whose
only payoff is raw ANN speed — irrelevant at a few thousand chunks per repo. Chroma also isolates
projects cleanly: deleting a project is one `delete_collection` call.

**Consequences.** Chroma pulls a heavier dependency tree (its own SQLite store). Verified to
install on this venv's Python 3.14.6 via a `cp39-abi3` wheel, so no build toolchain is needed.
The `VectorStore` wrapper in `vectorstore/store.py` exposes only `add`, `search`, `delete`, and
`count`, so a FAISS implementation stays swappable if it's ever warranted.

---

## ADR-002 — GitHub zipball download instead of `git clone`
**Status:** Accepted

**Context.** Input is a public repo URL; we need its source on disk.

**Decision.** `GET https://api.github.com/repos/{owner}/{repo}/zipball/{ref}`, streamed to a
temp file and extracted with path-traversal and symlink guards.

**Why.** No dependency on a `git` binary being installed and on `PATH` — a real portability
concern on Windows and in containers. The API resolves the default branch on its own (no
guessing `main` vs `master`). Streaming download lets us enforce a hard size cap *during*
transfer and abort early, which `git clone` cannot do. History is irrelevant to us; a shallow
clone would just be a slower path to the same working tree.

**Consequences.** Unauthenticated GitHub API is limited to 60 requests/hour per IP, so
`GITHUB_TOKEN` is supported and the rate-limit error is called out distinctly. Zip extraction is
an attack surface — traversal and symlink checks are mandatory, not optional hardening.

---

## ADR-003 — Language-aware chunking with computed line numbers
**Status:** Accepted

**Context.** The brief requires citations with file paths "and, where practical, line numbers".
Splitters return character offsets, not lines.

**Decision.** `RecursiveCharacterTextSplitter.from_language()` where the extension maps to a
LangChain `Language`, plain recursive splitter otherwise, always with `add_start_index=True`.
Derive lines by counting newlines up to the offset.

**Why.** Language separators (`\nclass `, `\ndef `, `\nfunction `) keep declarations intact far
more often than blind character splitting, which is what makes a citation land on a whole
function instead of its tail. The newline-count conversion is exact, costs microseconds, and
needs no parser.

**Alternative rejected.** tree-sitter AST chunking gives true symbol boundaries. `tree-sitter`
and `tree-sitter-language-pack` both have working wheels for this venv, so it is *feasible* —
but it means a per-language node-type mapping, a fallback path for unsupported languages, and
handling of oversized functions. Deferred to a stretch task; the recursive splitter is roughly
80% of the benefit for roughly 10% of the effort.

---

## ADR-004 — Chunks are embedded with a synthetic header
**Status:** Accepted

**Context.** Questions frequently reference *paths* and *symbol names* ("where is auth handled",
"explain `validate_token`"). Raw code bodies often contain neither near the relevant lines.

**Decision.** Prepend `# File: {path} (lines a-b) [{language}]` and a regex-extracted
`# Symbols:` line to the text that gets **embedded**. Chroma stores the verbatim body separately
so citations and snippet display show real code, unmodified.

**Why.** It puts the exact tokens users search with into the embedded vector at negligible cost.
Regex symbol extraction (`def|class|function|const|func|type|public`) is crude but effective and
requires no parsing.

**Consequences.** Header tokens dilute the embedding slightly. Embedded text and stored text
diverge — flagged here because it's a genuine footgun for anyone reading `chunker.py` cold.

---

## ADR-005 — Stream the draft, then revise visibly
**Status:** Accepted

**Context.** Two required features conflict directly. Streaming means the user sees tokens as
they generate. The Review agent judges the *finished* answer and may reject it. You cannot both
stream an answer and guarantee it passed review.

**Options.**
- **A. Withhold until reviewed.** Correct-by-construction, but the user stares at a spinner for
  the full generation, and "streaming responses" becomes a lie.
- **B. Stream the draft; on rejection, emit a `revision` event, clear the draft, and stream the
  replacement.**
- **C. Review only in a lightweight, non-blocking way** — no revision loop. Cheapest, but guts
  the Review agent into a rubber stamp, which the brief explicitly does not ask for.

**Decision.** Option B.

**Why.** It preserves both features honestly. The revision path is the uncommon case, and when
it fires, showing *"Refining answer with additional context…"* is not a wart — it's a visible
demonstration that the review loop does real work. Option C was rejected because a Review agent
that cannot send work back is theatre.

**Consequences.** The UI must be able to discard already-rendered tokens; the chat component
keeps the draft in a placeholder container that can be reset. Revisions are capped at 2 so a
disagreeing model pair cannot loop indefinitely.

---

## ADR-006 — The directory question bypasses the vector store
**Status:** Accepted

**Context.** *"Explain the project's file directory"* is one of the five target questions.
Answering it through similarity search means retrieving ~12 chunks of a tree document and
reconstructing the rest — which is exactly the shape of question RAG hallucinates on, inventing
plausible directories that do not exist.

**Decision.** The Coordinator's `directory` route skips Retrieval entirely and loads the
persisted tree JSON built during ingestion. The Explanation agent annotates a structure it has
been handed in full.

**Why.** We already know the complete, exact answer at index time. Round-tripping ground truth
through an approximate retriever can only lose information. This is retrieval-*augmented*
generation, not retrieval-*only* generation — deterministic sources should be used when they
exist.

**Consequences.** A second, non-vector context source that both the graph and the tests must
handle. For very large repos the tree is depth- and count-pruned before injection, with the
elision marked explicitly in the text so the model doesn't present a truncated tree as complete.

---

## ADR-007 — NDJSON streaming, not SSE
**Status:** Accepted

**Decision.** `/chat` returns `application/x-ndjson`; one JSON object per line.

**Why.** The only client is Streamlit, i.e. Python `httpx`/`requests`. `iter_lines()` +
`json.loads` is a complete and correct parser in two lines. SSE would add `event:`/`data:`/
`\n\n` framing and multi-line-payload rules for zero gain, since no browser `EventSource` is
involved. Typed line objects also let us multiplex `phase`, `token`, `citations`, `revision`,
and `error` down one connection.

**Consequences.** Not directly consumable by a browser `EventSource` if a JS frontend is ever
added. Acceptable — that would be a rewrite of the client layer anyway.

---

## ADR-008 — In-memory conversation and job stores
**Status:** Accepted

**Context.** Chat history and index-job status need somewhere to live.

**Decision.** Process-local dicts behind `ConversationStore` and `JobStore` interfaces.
Projects and vectors, by contrast, persist to disk.

**Why.** Single-instance, single-user assessment scope. Correctly split: losing chat scrollback
on restart is a minor annoyance, whereas losing an indexed repo would mean re-paying embedding
cost, so *that* is on disk.

**Consequences.** Chat history resets when uvicorn restarts, and this will not survive multiple
workers (`--workers > 1`) — documented as a known limitation, with SQLite as the drop-in
upgrade behind the same interface.

---

## ADR-009 — Background job + polling, not WebSockets, for index progress
**Status:** Accepted

**Decision.** `POST /projects` returns `202` with a `project_id` immediately; indexing runs in
`BackgroundTasks`; the frontend polls `GET /projects/{id}` on a ~1.5 s cadence and drives
`st.progress`.

**Why.** Streamlit's execution model reruns the whole script on interaction and has no natural
place to own a long-lived socket. Polling is a few lines, degrades gracefully, and is trivially
debuggable with `curl`. Indexing is a one-shot per project, not a high-frequency stream.

**Consequences.** Up to ~1.5 s of progress lag. `BackgroundTasks` ties the job to the process,
so a restart mid-index leaves a `failed` project the user must re-add; the failure is reported
rather than left hanging.

---

## ADR-010 — Two model tiers
**Status:** Accepted

**Decision.** `OPENAI_FAST_MODEL` for Coordinator and Review (structured, mechanical, called on
every turn), `OPENAI_CHAT_MODEL` for Explanation (the only node whose prose the user reads).

**Why.** Roughly 3 of 4 LLM calls per turn are classification and verification, which small
models do well. Spending a frontier model on JSON routing is pure waste. Both are env-configured
so the split can be collapsed to one model for a demo.

**Consequences.** Two model configs to keep valid. Verify current OpenAI model IDs before
setting defaults in `config.py` — a stale ID is a runtime 404, not a startup error.

---

## ADR-011 — Python 3.14 venv retained
**Status:** Accepted

**Context.** The existing `venv/` is Python 3.14.6, new enough to plausibly lack wheels for
native dependencies. This was checked rather than assumed, before committing to the stack.

**Decision.** Keep it. Verified resolutions: `faiss-cpu 1.14.3` (native `cp314` wheel),
`chromadb 1.5.9` (`cp39-abi3`), `tree-sitter 0.26.0` (`cp314`), plus pure-Python
`langgraph 1.2.10`, `langchain-openai 1.4.1`, `fastapi 0.141.1`, `streamlit 1.60.0`.

**Consequences.** No blocker. Note that **LangGraph and langchain-openai are both on 1.x
majors** — verify the current import paths and the structured-output and streaming APIs against
installed package source during Phase 3 rather than relying on older tutorial patterns. If any
transitive dependency later fails to build on 3.14, the fallback is a 3.12 venv; nothing in the
design depends on 3.14-specific behavior.
