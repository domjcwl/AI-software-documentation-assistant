# Architecture

## 1. System overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  Streamlit UI (frontend/)                                           │
│  sidebar: add repo · project switcher · index progress bar          │
│  main:    chat transcript · streaming answer · citation expanders   │
└───────────────┬─────────────────────────────────────────────────────┘
                │ HTTP  (JSON + NDJSON stream)
┌───────────────▼─────────────────────────────────────────────────────┐
│  FastAPI (backend/)                                                 │
│                                                                     │
│  /health   /projects (POST,GET)   /projects/{id}   /chat (stream)   │
│                                                                     │
│  ┌── Ingestion pipeline ────────┐   ┌── LangGraph agent runtime ──┐  │
│  │ fetch → scan → chunk → embed │   │ Coordinator → Retrieval →   │  │
│  │        → persist             │   │ Explanation → Review        │  │
│  └────────────┬─────────────────┘   └──────────┬──────────────────┘  │
└───────────────┼────────────────────────────────┼────────────────────┘
                │                                │
        ┌───────▼────────┐              ┌────────▼────────┐
        │ ChromaDB       │              │ OpenAI API      │
        │ (persistent)   │              │ chat + embed    │
        │ 1 collection   │              └─────────────────┘
        │ per project    │
        └───────┬────────┘
        ┌───────▼──────────────────────────────┐
        │ data/repos/{project_id}/  extracted  │
        │ source, kept for snippet display     │
        │ and the deterministic directory map  │
        └──────────────────────────────────────┘
```

Two processes: `uvicorn` on `:8000`, `streamlit` on `:8501`. Streamlit holds **no** business
logic and **never** talks to OpenAI or Chroma directly — it is a pure client of the API. This
keeps the agent system testable without a browser and makes the API contract the single seam.

## 2. Repository layout (target)

```
backend/
  app/
    main.py                  FastAPI app, CORS, lifespan, router mounts
    config.py                pydantic-settings; reads .env
    errors.py                AppError hierarchy → HTTP problem responses
    schemas.py               all pydantic request/response models
    api/
      health.py              GET  /health
      projects.py            POST /projects, GET /projects, GET /projects/{id}
      chat.py                POST /chat  (NDJSON streaming)
    ingestion/
      github.py              URL parse, zipball download, safe extraction
      scanner.py             recursive walk, ignore rules, file classification
      chunker.py             language-aware splitting + line attribution
      manifest.py            file tree + repo-summary synthetic document
      pipeline.py            job orchestration, progress emission
    vectorstore/
      store.py               Chroma client, per-project collections, search
      embedder.py            OpenAI embeddings, batching, retry
    agents/
      state.py               GraphState TypedDict
      graph.py               node wiring, conditional edges, compile()
      coordinator.py         intent classification + query planning
      retrieval.py           multi-query search, dedupe, neighbor expansion
      explanation.py         answer generation (token-streaming node)
      review.py              grounding verdict + revise/finalize decision
      prompts.py             every system prompt, versioned in one place
    services/
      jobs.py                in-memory job registry + status snapshots
      conversations.py       per-(project, conversation) message history
  tests/
frontend/
  app.py                     Streamlit entry point
  api_client.py              typed HTTP client incl. NDJSON stream decoding
  state.py                   session_state helpers
  components/
    sidebar.py  chat.py  citations.py
data/                        gitignored
  chroma/                    persisted vector DB
  repos/{project_id}/        extracted source tree
planning/                    this folder
.env.example  requirements.txt  README.md
```

## 3. Ingestion pipeline

Runs as a FastAPI `BackgroundTasks` job. Each stage publishes progress into the job registry,
which the frontend polls.

### 3.1 Fetch (`ingestion/github.py`)

- Accept `https://github.com/{owner}/{repo}` with optional `/tree/{ref}` suffix, `.git` suffix,
  or bare `owner/repo`. Reject anything else with a precise message.
- Download `https://api.github.com/repos/{owner}/{repo}/zipball/{ref}` — resolves the default
  branch automatically and needs **no local `git` binary**.
- Stream to disk with a hard byte cap (`MAX_REPO_BYTES`, default 150 MB); abort past the cap.
- Extract with path traversal guards: reject entries containing `..` or absolute paths, and
  reject symlink entries. Strip GitHub's `{owner}-{repo}-{sha}/` top-level prefix.
- `GITHUB_TOKEN` optional; without it the API allows 60 requests/hour per IP. Surface a distinct
  error for 403-rate-limit vs 404-not-found/private.

### 3.2 Scan (`ingestion/scanner.py`)

Directory denylist (matched on any path segment):

```
.git  node_modules  dist  build  __pycache__  .venv  venv  env
.mypy_cache  .pytest_cache  .ruff_cache  .tox  .idea  .vscode
target  vendor  coverage  .next  .nuxt  out  site-packages  .terraform
```

File-level rejection, in order:
1. Binary — extension denylist plus a NUL-byte sniff of the first 8 KB.
2. Lockfiles and generated output — `package-lock.json`, `yarn.lock`, `poetry.lock`,
   `*.min.js`, `*.min.css`, `*.map`, `*.pb.go`, `*_pb2.py`, `*.snap`.
3. Oversized — `> MAX_FILE_BYTES` (default 400 KB).
4. Undecodable as UTF-8 (with `latin-1` fallback attempt before giving up).

Surviving files are classified into a `doc_type`, which the Retrieval agent later filters on:

| `doc_type` | Matches |
|---|---|
| `code` | `.py .js .ts .tsx .jsx .java .go .rs .rb .php .cs .cpp .c .h .kt .swift .scala .sh .sql` |
| `doc` | `.md .rst .txt .adoc` |
| `config` | `.json .yaml .yml .toml .ini .cfg`, `Dockerfile`, `Makefile`, `.github/workflows/*` |
| `manifest` | synthetic documents generated in §3.4 |

A global cap (`MAX_FILES`, default 3000) protects cost; if exceeded, keep files ranked by a
relevance heuristic (docs and configs first, then shallow paths, then larger source files) and
record the truncation in the project record so the UI can warn.

### 3.3 Chunk (`ingestion/chunker.py`)

`RecursiveCharacterTextSplitter.from_language(...)` when the extension maps to a LangChain
`Language` enum member, otherwise the plain recursive splitter. Language-aware separators keep
functions and classes intact far more often than naive character splitting.

- Code: `chunk_size=1200`, `chunk_overlap=150`.
- Docs/config: `chunk_size=1500`, `chunk_overlap=200`.
- Always `add_start_index=True`.

**Line attribution** — the mechanism behind citations. The splitter gives a character offset,
not a line number, so convert:

```python
start_line = source[:chunk.start_index].count("\n") + 1
end_line   = start_line + chunk.page_content.count("\n")
```

**Embedded text ≠ stored text.** Each chunk is embedded with a synthetic header so that
path-shaped and language-shaped questions retrieve well:

```
# File: src/auth/session.py (lines 40-88) [python]
# Symbols: create_session, validate_token
<verbatim chunk body>
```

`Symbols` come from a cheap per-language regex over `def|class|function|const|func|public`
declarations — no parsing required, and it measurably improves "explain function X" retrieval.

Chunk metadata (all stored in Chroma, all available for citation and filtering):

```
project_id, path, doc_type, language, chunk_index, start_line, end_line, symbols, sha
```

### 3.4 Synthetic manifest documents (`ingestion/manifest.py`)

Beyond raw source, generate and index three documents. These are what make "summarize the
architecture" and "explain the file directory" answerable rather than hallucinated:

1. **`__directory_tree__`** — the full pruned tree with per-directory file counts and total LOC.
2. **`__repo_summary__`** — detected languages by LOC share, entry points (`main.py`, `app.py`,
   `index.ts`, `cmd/*/main.go`, `manage.py`), dependency manifests found, presence of
   Dockerfile/CI/tests, and the first ~200 lines of the root README.
3. **`__file_index__`** — one line per kept file: path, language, LOC, extracted top-level
   symbols. Chunked like any other document.

The tree is also persisted verbatim as JSON on the project record, because the `directory`
route reads it **directly** rather than through vector search (see ADR-006).

### 3.5 Embed & persist

- Model from config (default `text-embedding-3-small`), batched at 96 inputs per request.
- Tenacity-style exponential backoff on 429/5xx; on permanent failure the job transitions to
  `failed` with the batch index recorded, and partial vectors are dropped so a project is never
  half-indexed and silently wrong.
- One Chroma collection per project: `proj_{project_id}`. Deleting a project drops the
  collection and the extracted tree together.

### 3.6 Job status model

```
queued → fetching → scanning → chunking → embedding → ready
                                                   ↘ failed
```

Each snapshot carries `stage`, `percent`, `message`, `files_scanned`, `files_indexed`,
`chunks_embedded`, `error`. Progress percent is a weighted blend (fetch 10%, scan 10%,
chunk 15%, embed 65%) so the bar advances smoothly instead of sitting at 0 during embedding.

## 4. Agent orchestration (LangGraph)

### 4.1 Graph

![LangGraph agent workflow](langgraph_flow.png)

*Rendered from the compiled graph by `generate_graph_diagram.py` — it reads the nodes and
edges out of `build_graph(...).get_graph()` rather than being drawn by hand, and refuses to
run if the graph gains a node the layout doesn't know about. Regenerate after changing the
graph:*

```bash
venv/Scripts/python.exe planning/generate_graph_diagram.py
```

The same structure as ASCII:

```
                    ┌─────────────┐
        START ─────►│ Coordinator │
                    └──────┬──────┘
                           │ route
          ┌────────────────┼─────────────────┬──────────────┐
          │                │                 │              │
    code_qa /        directory          clarify         (error)
  architecture /          │                 │              │
  modification            │                 │              │
          │               │                 │              │
    ┌─────▼─────┐   ┌─────▼──────┐    ┌─────▼─────┐        │
    │ Retrieval │   │ Directory  │    │ Clarify   │        │
    └─────┬─────┘   │ (no vector │    │ (ask user)│        │
          │         │  search)   │    └─────┬─────┘        │
          │         └─────┬──────┘          │              │
    ┌─────▼───────────────▼─────┐           │              │
    │      Explanation          │           │              │
    │   (streams tokens out)    │           │              │
    └─────────────┬─────────────┘           │              │
                  │                         │              │
            ┌─────▼─────┐                   │              │
            │  Review   │                   │              │
            └─────┬─────┘                   │              │
       revise ◄───┤                         │              │
    (back to      │ finalize                │              │
     Retrieval,   ▼                         ▼              ▼
     max 2)      END ◄─────────────────────────────────────┘
```

### 4.2 Shared state

```python
class GraphState(TypedDict):
    project_id: str
    conversation_id: str
    question: str
    history: list[Turn]                # last N turns, for pronoun/context resolution
    route: Route                       # code_qa|architecture|directory|modification|clarify
    search_queries: list[str]          # planned by Coordinator, refined by Review
    retrieved: list[RetrievedChunk]
    draft_answer: str
    verdict: ReviewVerdict | None      # grounded, issues, suggested_queries
    revision_count: int                # hard ceiling of 2
    final_answer: str
    citations: list[Citation]
    error: str | None
```

### 4.3 Coordinator agent

Single structured-output LLM call. Given the question plus the last 3 turns it returns:

- `route` — one of the five values above.
- `search_queries` — 2–4 rewritten, self-contained retrieval queries. This is where
  *"explain this function"* becomes *"implementation of validate_token in auth/session.py"*
  using conversation history. Follow-up questions are useless to a vector store without it.
- `doc_type_filter` — optional bias, e.g. `architecture` weights `doc` and `manifest`.
- `needs_clarification` + `clarifying_question` when the question has no resolvable referent.

Route-specific downstream behavior:

| Route | Retrieval behavior | Explanation behavior |
|---|---|---|
| `code_qa` | multi-query over all doc types | direct answer with inline citations |
| `architecture` | forces `__repo_summary__` + `__file_index__` into context, plus top code hits for entry points | layered narrative: purpose → major modules → data flow → key dependencies |
| `directory` | **bypasses vector search**, reads the persisted tree JSON | annotates the tree, grouping by responsibility |
| `modification` | two search passes: (a) where the target subsystem lives, (b) an existing analogous feature to imitate | ordered change plan: files to touch, what to add, integration points, risks |
| `clarify` | none | returns the clarifying question, ends the graph |

### 4.4 Retrieval agent

1. Run every planned query against the project collection, `k=8` each.
2. Merge and dedupe on `(path, chunk_index)`; score by best rank across queries with a small
   bonus for appearing in multiple queries (reciprocal-rank fusion).
3. **Neighbor expansion** — for the top 3 hits, also pull `chunk_index ± 1` from the same file
   so the model sees the function's surroundings rather than a severed fragment.
4. Cap at 12 chunks / ~6000 tokens, trimming lowest-scored first.
5. If the best score is below `MIN_RELEVANCE`, set a `low_confidence` flag — Explanation is
   then instructed to say plainly that the codebase doesn't appear to cover this, instead of
   confabulating. **Empty retrieval must produce "I couldn't find this", never a guess.**

### 4.5 Explanation agent

The only node that streams. Emits tokens as they arrive via LangGraph's message-stream mode,
which the `/chat` endpoint forwards to the browser. Prompt rules:

- Cite as `` `path/to/file.py:40-88` `` inline, at the point the claim is made.
- Never state a fact about the code that isn't in the provided context.
- Prefer showing a short real snippet over paraphrasing it.
- If context is insufficient, say so and name what would be needed.

### 4.6 Review agent

Runs on the completed draft with the same retrieved context. Returns structured output:

```python
class ReviewVerdict(BaseModel):
    grounded: bool                  # every claim traceable to context
    cites_files: bool               # ≥1 real citation present, paths verified to exist
    issues: list[str]
    suggested_queries: list[str]    # non-empty only when requesting a revision
    polished_answer: str | None     # clarity-only rewrite when grounded is already True
```

Two exits:
- **finalize** — grounded and cited. Use `polished_answer` when supplied, else the draft.
  Attach the citation list built from the chunks actually referenced in the text.
- **revise** — ungrounded or uncited, and `revision_count < 2`. Loop back to Retrieval with
  `suggested_queries`. At the ceiling, finalize with an explicit caveat rather than looping.

Citation paths are validated against the on-disk file list; a citation to a file that doesn't
exist is stripped and counted as a grounding failure.

## 5. Streaming design

`POST /chat` returns `application/x-ndjson` — one JSON object per line. NDJSON over SSE because
the client is Python `requests`/`httpx`, where `iter_lines()` + `json.loads` is trivially
correct, while SSE framing would need a parser for no benefit here.

Event types: `phase`, `token`, `citations`, `revision`, `final`, `error`. See
[api_contract.md](api_contract.md) for exact shapes.

**The streaming ↔ review tension** (ADR-005): the Review agent can reject an answer that has
already been streamed to the user. Rather than withhold output until review passes — which
would eliminate the perceived-latency benefit of streaming — the draft streams immediately,
and if Review requests a revision the backend emits a `revision` event. The UI then clears the
draft, shows *"Refining answer with additional context…"*, and streams the replacement. The
`phase` events (`coordinating`, `retrieving`, `explaining`, `reviewing`) double as the visible
demonstration that four distinct agents are actually running.

## 6. Persistence

| Data | Where | Lifetime |
|---|---|---|
| Vectors + chunk metadata | `data/chroma/` (Chroma PersistentClient) | until project deleted |
| Extracted source | `data/repos/{project_id}/` | until project deleted |
| Project registry | `data/projects.json`, written atomically | permanent |
| Job status | in-process dict | process lifetime |
| Conversation history | in-process dict, keyed `(project_id, conversation_id)` | process lifetime |

In-memory history is a deliberate scope call for a single-instance assessment build; the
`ConversationStore` interface is written so a SQLite implementation is a drop-in swap
(ADR-008). Projects survive restart because vectors and the registry are on disk — only chat
scrollback is lost.

## 7. Configuration

```
OPENAI_API_KEY=            # required
GITHUB_TOKEN=              # optional, raises GitHub rate limit
OPENAI_CHAT_MODEL=         # main reasoning model
OPENAI_FAST_MODEL=         # coordinator + review
OPENAI_EMBED_MODEL=text-embedding-3-small
CHROMA_DIR=./data/chroma
REPO_DIR=./data/repos
MAX_REPO_BYTES=157286400
MAX_FILE_BYTES=409600
MAX_FILES=3000
RETRIEVAL_K=8
MAX_CONTEXT_CHUNKS=12
MAX_REVISIONS=2
BACKEND_URL=http://localhost:8000
```

> Confirm the current OpenAI chat model IDs at implementation time and set the defaults in
> `config.py` accordingly — model names churn, and a stale default is a silent 404 at runtime.
> Everything is read through `config.py`; no model string is hardcoded in agent code.

## 8. Error handling

Backend raises typed `AppError`s that map to a consistent JSON body
(`{"error": {"code", "message", "hint"}}`). The frontend renders `message` + `hint` in an
`st.error`, never a raw traceback.

| Code | Trigger | User-facing hint |
|---|---|---|
| `invalid_repo_url` | unparseable URL | "Use https://github.com/owner/repo" |
| `repo_not_found` | 404 | "Repository not found, or it is private. Only public repos are supported." |
| `github_rate_limited` | 403 + rate-limit headers | "GitHub rate limit hit. Set GITHUB_TOKEN in .env or retry in N minutes." |
| `repo_too_large` | byte cap exceeded | "Repository exceeds the 150 MB limit." |
| `no_indexable_files` | scan yields 0 files | "No supported source files found after filtering." |
| `project_not_ready` | chat before index completes | "Indexing is still running — N% complete." |
| `openai_auth` | 401 from OpenAI | "OPENAI_API_KEY is missing or invalid." |
| `openai_rate_limited` | 429 after retries | "OpenAI rate limit. Retry shortly." |
| `internal_error` | anything else | logged with a correlation id shown to the user |

## 9. Non-goals

Private repos and OAuth; incremental re-indexing on push; multi-user auth and isolation;
cross-repository queries in a single answer; write access to the analyzed codebase; horizontal
scaling. Each is a deliberate exclusion, not an oversight — recorded so scope stays honest.
