"""Review agent: grounding check, citation validation, revise-or-finalise.
See planning/architecture.md §4.6.

The citation check is deliberately not left to the LLM alone. Cited paths
are parsed out of the draft and verified against the chunks that were
actually retrieved — a model asserting "yes, it cited real files" is not
evidence that the files exist.
"""

from __future__ import annotations

import logging
import re

from app.agents import prompts
from app.agents.llm import fast_model
from app.agents.retrieval import format_context
from app.agents.state import Citation, GraphState, ReviewVerdict
from app.config import Settings

logger = logging.getLogger("app.agents.review")

# Matches `path/to/file.py:12-40`, `path/to/file.py:12`, and a bare
# `path/to/file.py`. Bare paths count because the `modification` route
# legitimately names whole files to change without pointing at a line
# range — treating those as uncited caused pointless revision loops.
_PATH = r"[\w./\-]+\.[\w]+|__[a-z_]+__"
_CITATION_RE = re.compile(rf"`?({_PATH})(?::(\d+)(?:\s*[-–]\s*(\d+))?)?`?")

_MAX_REVISION_NOTE = (
    "\n\n_Note: this answer could not be fully verified against the repository's "
    "indexed contents. Treat the details above with corresponding caution._"
)


def review(state: GraphState, settings: Settings) -> dict:
    draft = (state.get("draft_answer") or "").strip()
    chunks = state.get("retrieved") or []
    route = state.get("route", "code_qa")

    if not draft:
        return _finalise(
            "I wasn't able to generate an answer for that. Please try rephrasing the question.",
            [],
            None,
        )

    citations = extract_citations(draft, chunks, route=route)

    context = state.get("directory_tree") if route == "directory" else format_context(chunks)
    reviewer = fast_model(settings).with_structured_output(ReviewVerdict)
    messages = [
        ("system", prompts.REVIEW_SYSTEM),
        (
            "user",
            prompts.REVIEW_USER.format(
                context=context or "(none)", question=state["question"], draft=draft
            ),
        ),
    ]

    try:
        verdict: ReviewVerdict = reviewer.invoke(messages)
    except Exception:
        # Never let a reviewer outage swallow a usable answer — ship the
        # draft rather than failing the whole request.
        logger.exception("Review failed; finalising the draft unreviewed")
        return _finalise(draft, citations, None)

    # The model's own citation claim is advisory; the parsed-and-validated
    # citation list is authoritative.
    verdict.cites_files = bool(citations)

    revision_count = state.get("revision_count", 0)
    if verdict.declines_for_lack_of_context:
        # Correctly reporting "this repo doesn't contain that" is the
        # desired outcome, not a failure — revising it would only pressure
        # the next draft into inventing something.
        needs_revision = False
    else:
        # `directory` is exempt alongside `clarify`: its context is the
        # complete file tree rather than retrieved chunks (ADR-006), so
        # there is no chunk set for `path:line` citations to validate against.
        citation_exempt = route in ("clarify", "directory")
        needs_revision = not verdict.grounded or (not citations and not citation_exempt)

    if needs_revision and revision_count < settings.max_revisions:
        logger.info(
            "Review requested revision %d/%d: %s",
            revision_count + 1,
            settings.max_revisions,
            "; ".join(verdict.issues) or "ungrounded or uncited",
        )
        return {
            "verdict": verdict,
            "revision_count": revision_count + 1,
            "search_queries": verdict.suggested_queries or state.get("search_queries") or [],
        }

    answer = draft
    if verdict.polished_answer and verdict.grounded:
        polished = verdict.polished_answer.strip()
        # Only accept a rewrite that preserves the citations; a "polish"
        # that drops them is a regression, not an improvement.
        if not citations or extract_citations(polished, chunks, route=route):
            answer = polished
            citations = extract_citations(polished, chunks, route=route) or citations

    if needs_revision:
        answer += _MAX_REVISION_NOTE

    return _finalise(answer, citations, verdict)


def _finalise(answer: str, citations: list[Citation], verdict: ReviewVerdict | None) -> dict:
    return {"final_answer": answer, "citations": citations, "verdict": verdict}


def extract_citations(
    text: str, chunks: list[str] | list, *, route: str = "code_qa"
) -> list[Citation]:
    """Parse `path:start-end` citations out of an answer, keeping only
    those whose path was actually retrieved. Synthetic manifest documents
    (`__repo_summary__` etc.) are never surfaced as citations — they are
    generated summaries, not files a user could open."""
    known_paths = {getattr(c, "path", None) for c in chunks}
    known_paths.discard(None)

    seen: set[tuple[str, int, int]] = set()
    citations: list[Citation] = []

    line_spans = _line_spans_by_path(chunks)

    for match in _CITATION_RE.finditer(text or ""):
        path, start_raw, end_raw = match.group(1), match.group(2), match.group(3)
        if path.startswith("__") or path not in known_paths:
            continue
        if start_raw is None:
            # Bare path: attribute it to the span actually retrieved for
            # that file, so the citation still points somewhere real.
            start, end = line_spans.get(path, (1, 1))
        else:
            start = int(start_raw)
            end = int(end_raw) if end_raw else start
        if end < start:
            start, end = end, start
        key = (path, start, end)
        if key in seen:
            continue
        seen.add(key)
        citations.append(Citation(path=path, start_line=start, end_line=end))

    return citations


def _line_spans_by_path(chunks) -> dict[str, tuple[int, int]]:
    """Widest retrieved line span per file, used to give a bare-path
    citation a concrete range."""
    spans: dict[str, tuple[int, int]] = {}
    for chunk in chunks:
        path = getattr(chunk, "path", None)
        if path is None:
            continue
        start = getattr(chunk, "start_line", 1)
        end = getattr(chunk, "end_line", start)
        if path in spans:
            spans[path] = (min(spans[path][0], start), max(spans[path][1], end))
        else:
            spans[path] = (start, end)
    return spans
