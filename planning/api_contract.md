# API Contract

Base URL: `http://localhost:8000`. This contract is frozen once Phase 2 lands — frontend and
backend are built against it independently. Changes require an edit here **first**.

All errors share one body:

```json
{ "error": { "code": "repo_not_found",
             "message": "Repository owner/repo was not found.",
             "hint": "Check the URL. Only public repositories are supported." } }
```

---

## `GET /health`

```json
{ "status": "ok", "version": "0.1.0", "openai_configured": true,
  "chroma_ok": true, "projects_indexed": 3 }
```

`200` when the process is up and Chroma opens. `openai_configured` reports key *presence*, not
validity — no live API call on a healthcheck. The frontend calls this on load and shows a
banner if `openai_configured` is false, so a missing key is caught before a repo is indexed.

---

## `POST /projects` → `202 Accepted`

Request:
```json
{ "repo_url": "https://github.com/tiangolo/fastapi", "ref": null }
```

`ref` optional (branch/tag/SHA); default branch when null. Accepted `repo_url` forms:
full URL, URL with `/tree/{ref}`, URL with `.git`, or bare `owner/repo`.

Response:
```json
{ "project_id": "a3f1c2d4", "name": "tiangolo/fastapi", "status": "queued" }
```

Indexing starts in the background immediately. Re-submitting an already-indexed repo returns the
existing `project_id` with its current status rather than re-indexing; `?force=true` re-indexes.

Errors: `invalid_repo_url` (400), `repo_not_found` (404), `github_rate_limited` (429),
`repo_too_large` (413).

---

## `GET /projects`

```json
{ "projects": [
  { "project_id": "a3f1c2d4", "name": "tiangolo/fastapi",
    "repo_url": "https://github.com/tiangolo/fastapi", "ref": "master",
    "status": "ready", "files_indexed": 412, "chunks": 3877,
    "created_at": "2026-08-01T10:22:03Z" } ] }
```

Powers the sidebar project switcher. Only `status: "ready"` projects are selectable for chat.

---

## `GET /projects/{project_id}`

Polled at ~1.5 s while indexing.

```json
{ "project_id": "a3f1c2d4", "name": "tiangolo/fastapi", "status": "embedding",
  "stage": "embedding", "percent": 62, "message": "Embedding chunks 2400/3877",
  "files_scanned": 412, "files_indexed": 412, "chunks_embedded": 2400,
  "truncated": false, "error": null }
```

`status` ∈ `queued | fetching | scanning | chunking | embedding | ready | failed`.
When `failed`, `error` holds the same `{code, message, hint}` object used elsewhere.
`truncated: true` means the `MAX_FILES` cap was hit — the UI shows a warning that coverage is
partial.

---

## `DELETE /projects/{project_id}` → `204`

Drops the Chroma collection, the extracted tree under `data/repos/{id}/`, and the registry
entry. Idempotent.

---

## `POST /chat` → `200`, `application/x-ndjson`

Request:
```json
{ "project_id": "a3f1c2d4",
  "conversation_id": "c-9f21",
  "message": "How does authentication work?" }
```

`conversation_id` is generated client-side per project tab; omitting it starts a new
conversation. Server-side history is capped at the last 10 turns.

Errors before the stream opens are ordinary JSON: `project_not_found` (404),
`project_not_ready` (409, `message` includes current percent), `openai_auth` (401).
Once the stream has opened, failures arrive as an `error` line and the stream closes.

### Event lines

One JSON object per line, newline-terminated, flushed as produced.

**`phase`** — drives the "what the agents are doing" indicator.
```json
{"type":"phase","phase":"retrieving","detail":"Searching 3 queries across 3877 chunks"}
```
`phase` ∈ `coordinating | retrieving | explaining | reviewing | revising | done`.

**`route`** — emitted once, after the Coordinator decides. Shown as a small badge.
```json
{"type":"route","route":"code_qa","queries":["session token validation","login handler","auth middleware"]}
```

**`sources`** — emitted after retrieval, **before** tokens, so the citation expander can render
while the answer is still generating. `score` is cosine similarity in `[0, 1]` (higher is
better); `snippet` is the first 600 characters of the chunk.
```json
{"type":"sources","sources":[
  {"path":"app/security.py","start_line":40,"end_line":88,"language":"python",
   "doc_type":"code","score":0.83,
   "snippet":"def verify_password(plain, hashed):\n    ..."}]}
```

The `directory` route emits `sources` with an empty list — it answers from the complete file
tree persisted at index time rather than from vector search (ADR-006).

**`token`** — one incremental chunk of answer text.
```json
{"type":"token","text":"Authentication is handled by "}
```

**`revision`** — Review rejected the draft (ADR-005). The UI **discards all tokens received so
far**, shows `reason`, and renders the tokens that follow as the new answer.
```json
{"type":"revision","reason":"Draft referenced files not present in retrieved context.","attempt":1}
```

A `revision` is always followed by a fresh `sources` event, then new `token` events.

**`final`** — terminal success. `answer` is the authoritative full text; the client should
replace the accumulated token buffer with it to guarantee the transcript matches the server
(it differs from the streamed tokens whenever Review returns a polished rewrite).
```json
{"type":"final","answer":"Authentication is handled by ...",
 "citations":[{"path":"app/security.py","start_line":40,"end_line":88}],
 "route":"code_qa","revisions":0,"grounded":true,"low_confidence":false}
```

`citations` contains only paths that were actually retrieved — the Review agent parses them out
of the answer text and discards any the model invented. `low_confidence` is true when the best
retrieval score fell below the relevance floor, i.e. the answer is likely "this repo doesn't
cover that". The planned `usage` field is **not implemented** — per-request token accounting is
still open (see progress.md).

**`error`** — terminal failure.
```json
{"type":"error","error":{"code":"openai_rate_limited","message":"...","hint":"..."}}
```

### Ordering guarantees

1. `phase:coordinating` is always first.
2. `route` precedes any `sources` or `token`.
3. `sources` precedes the `token` events it supports.
4. Exactly one terminal line — `final` or `error` — and it is last.
5. `clarify` route: no `sources`; the clarifying question arrives as `token`s then `final` with
   empty `citations`.

---

## `GET /projects/{project_id}/file`

Backs "view full file" from a citation expander.

Query: `path` (repo-relative, required), `start_line`, `end_line` (optional window).

```json
{ "path": "app/security.py", "language": "python", "total_lines": 210,
  "start_line": 40, "end_line": 88, "content": "def verify_password(...)..." }
```

Path is resolved against the project root and rejected with `400 invalid_path` if it escapes —
same traversal guard as extraction.
