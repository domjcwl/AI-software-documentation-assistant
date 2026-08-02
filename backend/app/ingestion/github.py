"""Fetch a public GitHub repository's source tree without needing a local
`git` binary. See planning/decisions.md ADR-002 for why zipball download
was chosen over `git clone`.

Repo URL parsing lives in app.services.repo_url (parse_repo_url) — this
module starts from an already-parsed (owner, repo, ref) and is responsible
for everything from there: existence/rate-limit checks, the size-capped
streaming download, and safe extraction.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from app.config import Settings
from app.errors import (
    GithubRateLimitedError,
    InternalError,
    NoIndexableFilesError,
    RepoNotFoundError,
    RepoTooLargeError,
)

_API_ROOT = "https://api.github.com"


def fetch_repo(owner: str, repo: str, ref: str | None, dest_dir: Path, settings: Settings) -> str:
    """Download and safely extract a public GitHub repo into dest_dir.

    dest_dir is created if missing. Returns the resolved ref (the branch
    name GitHub reports as default, when the caller didn't pin one).
    """
    metadata = get_repo_metadata(owner, repo, settings)
    resolved_ref = ref or metadata.get("default_branch") or "HEAD"

    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ghfetch_") as tmp:
        zip_path = Path(tmp) / "repo.zip"
        download_zipball(owner, repo, resolved_ref, zip_path, settings)
        safe_extract(zip_path, dest_dir)

    return resolved_ref


def get_repo_metadata(owner: str, repo: str, settings: Settings) -> dict:
    """GET /repos/{owner}/{repo}. Also serves as the existence check
    before spending time on a full zipball download."""
    url = f"{_API_ROOT}/repos/{owner}/{repo}"
    try:
        resp = httpx.get(url, headers=_github_headers(settings), timeout=15.0)
    except httpx.HTTPError as exc:
        raise InternalError(f"Could not reach the GitHub API: {exc}") from exc

    if resp.status_code == 404:
        raise RepoNotFoundError(f"Repository {owner}/{repo} was not found, or it is private.")
    _raise_if_rate_limited(resp)
    if resp.status_code != 200:
        raise InternalError(f"GitHub API returned {resp.status_code} for {owner}/{repo}.")
    return resp.json()


def download_zipball(owner: str, repo: str, ref: str, dest_path: Path, settings: Settings) -> None:
    """Stream the zipball to dest_path, aborting once settings.max_repo_bytes
    is exceeded. Cleans up a partial file on any failure."""
    url = f"{_API_ROOT}/repos/{owner}/{repo}/zipball/{ref}"
    max_bytes = settings.max_repo_bytes
    success = False
    try:
        with httpx.stream(
            "GET", url, headers=_github_headers(settings), timeout=60.0, follow_redirects=True
        ) as resp:
            if resp.status_code == 404:
                raise RepoNotFoundError(f"Repository or ref not found: {owner}/{repo}@{ref}.")
            _raise_if_rate_limited(resp)
            if resp.status_code != 200:
                raise InternalError(f"GitHub returned {resp.status_code} downloading {owner}/{repo}@{ref}.")

            total = 0
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise RepoTooLargeError(
                            f"{owner}/{repo} exceeds the {max_bytes // (1024 * 1024)} MB limit."
                        )
                    f.write(chunk)
        success = True
    except httpx.HTTPError as exc:
        raise InternalError(f"Network error downloading {owner}/{repo}: {exc}") from exc
    finally:
        if not success:
            dest_path.unlink(missing_ok=True)


def safe_extract(zip_path: Path, dest_dir: Path) -> None:
    """Extract zip_path into dest_dir, stripping GitHub's
    '{owner}-{repo}-{sha}/' top-level folder and rejecting path
    traversal and symlink entries outright rather than sanitizing them."""
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if not names:
            raise NoIndexableFilesError("The downloaded archive was empty.")

        prefix = _common_top_level_prefix(names)

        for info in zf.infolist():
            rel = info.filename[len(prefix):] if prefix else info.filename
            if not rel:
                continue  # this entry *was* the prefix directory itself

            rel_path = PurePosixPath(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                continue  # reject traversal attempts
            if _is_symlink(info):
                continue  # never follow/materialize symlinks from an archive

            target = dest_dir / rel
            if info.filename.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def _github_headers(settings: Settings) -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


def _raise_if_rate_limited(resp: httpx.Response) -> None:
    if resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
        reset = resp.headers.get("x-ratelimit-reset")
        hint = "Set GITHUB_TOKEN in .env to raise the limit, or retry later."
        if reset and reset.isdigit():
            wait_s = max(0, int(reset) - int(time.time()))
            hint = f"Rate limit resets in ~{wait_s}s. Set GITHUB_TOKEN in .env to raise the limit."
        raise GithubRateLimitedError("GitHub API rate limit exceeded.", hint=hint)


def _common_top_level_prefix(names: list[str]) -> str:
    first_parts = names[0].split("/", 1)
    if len(first_parts) < 2:
        return ""
    prefix = first_parts[0] + "/"
    return prefix if all(n.startswith(prefix) for n in names) else ""


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return (mode & 0o170000) == 0o120000
