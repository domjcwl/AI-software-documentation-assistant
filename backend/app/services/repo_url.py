"""GitHub repo URL parsing.

This is the single source of truth for what counts as a valid repo_url —
the Phase 1 ingestion pipeline (planning/architecture.md §3.1) will reuse
this function as-is rather than re-deriving the rules.
"""

from __future__ import annotations

import re

from app.errors import InvalidRepoUrlError

# Accepts, host matched case-insensitively:
#   https://github.com/owner/repo
#   https://github.com/owner/repo/tree/{ref}
#   https://github.com/owner/repo.git
#   owner/repo   (no scheme/host at all)
_REPO_URL_RE = re.compile(
    r"^(?:(?:https?://)?(?:www\.)?github\.com/)?"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9._-]+?)"
    r"(?:\.git)?"
    r"(?:/tree/(?P<ref>[^/]+))?"
    r"/?$",
    re.IGNORECASE,
)


def parse_repo_url(repo_url: str) -> tuple[str, str, str | None]:
    """Parse a GitHub URL or 'owner/repo' shorthand into (owner, repo, ref).

    Raises InvalidRepoUrlError for anything else, including non-GitHub
    hosts and URLs pointing at something other than a repo root (e.g. a
    PR or file path).
    """
    candidate = repo_url.strip()
    if not candidate:
        raise InvalidRepoUrlError("repo_url must not be empty.")

    match = _REPO_URL_RE.match(candidate)
    if not match:
        raise InvalidRepoUrlError(f"Could not parse a GitHub owner/repo from {repo_url!r}.")

    return match.group("owner"), match.group("repo"), match.group("ref")
