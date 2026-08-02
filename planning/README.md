# Planning Index

All planning artifacts for the **AI Software Documentation Assistant** live in this folder.
Nothing planning-related goes anywhere else in the repo.

| Document | Purpose |
|---|---|
| [architecture.md](architecture.md) | System design: components, data flow, agent graph, storage layout |
| [decisions.md](decisions.md) | Architecture Decision Records — what was chosen and *why* |
| [api_contract.md](api_contract.md) | Frozen HTTP contract between FastAPI and Streamlit |
| [implementation_plan.md](implementation_plan.md) | Phased build order with per-phase exit criteria |
| [task_breakdown.md](task_breakdown.md) | Granular checklist of tasks with dependencies |
| [progress.md](progress.md) | Living status log — update as work lands |

## Project in one paragraph

A developer pastes a public GitHub repository URL. The backend downloads the repo, recursively
scans source files (skipping vendor/build noise), splits them into line-attributed chunks,
embeds them with OpenAI embeddings, and stores them in a per-project ChromaDB collection. The
developer then asks natural-language questions in a Streamlit chat. A LangGraph pipeline of four
specialized agents — Coordinator, Retrieval, Explanation, Review — answers the question, streams
the response token-by-token, and cites the exact files and line ranges it relied on.

## Ground rules for this project

1. **Every answer is cited.** An uncited claim about the codebase is a bug, not a style issue.
2. **The Review agent has teeth.** It can send the graph back to Retrieval, not just rubber-stamp.
3. **Failures are legible.** Every error surfaced to the user says what went wrong and what to do.
4. **No secrets in the repo.** `.env` is git-ignored; `.env.example` is committed.

## Conventions

- Read [decisions.md](decisions.md) before changing anything structural — several non-obvious
  choices (deterministic directory route, streaming-vs-review ordering, Chroma over FAISS) have
  recorded rationale and will look arbitrary without it.
- Update [progress.md](progress.md) at the end of every working session.
- When a decision is reversed, do not delete the ADR — mark it `Superseded` and add a new one.
