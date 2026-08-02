# Backend

FastAPI service for the AI Software Documentation Assistant. See `../planning/`
for design docs — this covers only how to run it.

## Run the dev server

From the project root, with the venv active:

    venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir backend --host 127.0.0.1 --port 8000

Then browse http://127.0.0.1:8000/docs for interactive API docs.

## Run tests

    venv\Scripts\python.exe -m pytest backend/tests -v

## Environment

Needs a real `OPENAI_API_KEY` in the project-root `.env` to actually index anything —
without one, `POST /projects` still fetches/scans/chunks a repo for real, then fails
cleanly at the embedding step with `error.code: "openai_auth"`. `GITHUB_TOKEN` is
optional; without it GitHub's public API is limited to 60 requests/hour/IP.

## Current scope

The backend is feature-complete: indexing and question answering both work end to end.

**Indexing** — `POST /projects` fetches a public GitHub repo (zipball, no `git` binary
needed), scans and chunks it with exact line-number attribution, builds directory-tree/
repo-summary/file-index manifest docs, embeds everything via OpenAI, and stores it in a
per-project Chroma collection. Runs as a background task with live progress over
`GET /projects/{id}`, and cleans up fully on `DELETE` or failure. Projects persist to
`data/projects.json` and survive a restart.

**Answering** — `POST /chat` streams NDJSON from a four-agent LangGraph pipeline:

    coordinator ─┬─ retrieval ─┬─ explanation ── review ─┬─ done
                 ├─ directory ─┘                         └─ retrieval (revise, max 2)
                 └─ clarify ─────────────────────────────── done

The Coordinator picks one of five routes and rewrites the question into self-contained
search queries using conversation history. Retrieval runs those queries, fuses their
rankings, and expands around the best hits. Explanation streams the answer. Review checks
grounding and independently verifies every cited path against what was actually
retrieved, sending the answer back for another retrieval pass if it doesn't hold up.

Try it: `curl -N -X POST localhost:8000/chat -H 'Content-Type: application/json' \
-d '{"project_id":"...","message":"How does authentication work?"}'`

**Not yet implemented:** the Streamlit frontend (`../planning/implementation_plan.md`
Phase 5), per-request token/cost accounting, and `GET /projects/{id}/file`.
