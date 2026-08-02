"""GitHub API calls are mocked with respx — no real network traffic here.
See test_pipeline.py for a test that hits the real GitHub API once, and
planning/decisions.md ADR-002 for why zipball download (not `git clone`)
was chosen, which is what makes safe_extract's traversal/symlink guards
security-critical rather than defensive boilerplate.
"""

from __future__ import annotations

import zipfile

import httpx
import pytest
import respx

from app.config import Settings
from app.errors import GithubRateLimitedError, RepoNotFoundError, RepoTooLargeError
from app.ingestion.github import download_zipball, fetch_repo, get_repo_metadata, safe_extract


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# --- get_repo_metadata ---


@respx.mock
def test_get_repo_metadata_success():
    respx.get("https://api.github.com/repos/octocat/hello").mock(
        return_value=httpx.Response(200, json={"default_branch": "main", "size": 42})
    )
    data = get_repo_metadata("octocat", "hello", _settings())
    assert data["default_branch"] == "main"


@respx.mock
def test_get_repo_metadata_404_raises_repo_not_found():
    respx.get("https://api.github.com/repos/octocat/missing").mock(return_value=httpx.Response(404))
    with pytest.raises(RepoNotFoundError):
        get_repo_metadata("octocat", "missing", _settings())


@respx.mock
def test_get_repo_metadata_rate_limited_raises():
    respx.get("https://api.github.com/repos/octocat/hello").mock(
        return_value=httpx.Response(
            403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "9999999999"}
        )
    )
    with pytest.raises(GithubRateLimitedError):
        get_repo_metadata("octocat", "hello", _settings())


# --- download_zipball ---


@respx.mock
def test_download_zipball_writes_file(tmp_path):
    respx.get("https://api.github.com/repos/octocat/hello/zipball/main").mock(
        return_value=httpx.Response(200, content=b"fake-zip-bytes")
    )
    dest = tmp_path / "repo.zip"
    download_zipball("octocat", "hello", "main", dest, _settings())
    assert dest.read_bytes() == b"fake-zip-bytes"


@respx.mock
def test_download_zipball_404_raises_and_cleans_up(tmp_path):
    respx.get("https://api.github.com/repos/octocat/hello/zipball/main").mock(return_value=httpx.Response(404))
    dest = tmp_path / "repo.zip"
    with pytest.raises(RepoNotFoundError):
        download_zipball("octocat", "hello", "main", dest, _settings())
    assert not dest.exists()


@respx.mock
def test_download_zipball_over_size_cap_raises_and_cleans_up(tmp_path):
    respx.get("https://api.github.com/repos/octocat/hello/zipball/main").mock(
        return_value=httpx.Response(200, content=b"x" * 1000)
    )
    dest = tmp_path / "repo.zip"
    with pytest.raises(RepoTooLargeError):
        download_zipball("octocat", "hello", "main", dest, _settings(max_repo_bytes=100))
    assert not dest.exists()


# --- safe_extract ---


def test_safe_extract_strips_prefix_and_keeps_legit_files(tmp_path):
    zip_path = tmp_path / "repo.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("octocat-hello-abc123/README.md", "hello")
        zf.writestr("octocat-hello-abc123/src/main.py", "print('hi')")

    dest = tmp_path / "extracted"
    safe_extract(zip_path, dest)

    assert (dest / "README.md").read_text() == "hello"
    assert (dest / "src" / "main.py").read_text() == "print('hi')"
    assert not (dest / "octocat-hello-abc123").exists()


def test_safe_extract_rejects_path_traversal(tmp_path):
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("octocat-hello-abc123/../../evil.txt", "pwned")
        zf.writestr("octocat-hello-abc123/safe.txt", "ok")

    dest = tmp_path / "extracted"
    safe_extract(zip_path, dest)

    assert not (tmp_path / "evil.txt").exists()
    assert (dest / "safe.txt").read_text() == "ok"


def test_safe_extract_rejects_symlinks(tmp_path):
    zip_path = tmp_path / "symlink.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        info = zipfile.ZipInfo("octocat-hello-abc123/link")
        info.external_attr = 0o120777 << 16  # unix symlink mode bits
        zf.writestr(info, "/etc/passwd")
        zf.writestr("octocat-hello-abc123/safe.txt", "ok")

    dest = tmp_path / "extracted"
    safe_extract(zip_path, dest)

    assert not (dest / "link").exists()
    assert (dest / "safe.txt").read_text() == "ok"


# --- fetch_repo (end to end, network mocked) ---


@respx.mock
def test_fetch_repo_end_to_end(tmp_path):
    respx.get("https://api.github.com/repos/octocat/hello").mock(
        return_value=httpx.Response(200, json={"default_branch": "main"})
    )

    build_path = tmp_path / "_build.zip"
    with zipfile.ZipFile(build_path, "w") as zf:
        zf.writestr("octocat-hello-main/README.md", "hi")
    zip_bytes = build_path.read_bytes()

    respx.get("https://api.github.com/repos/octocat/hello/zipball/main").mock(
        return_value=httpx.Response(200, content=zip_bytes)
    )

    dest = tmp_path / "extracted"
    resolved_ref = fetch_repo("octocat", "hello", None, dest, _settings())

    assert resolved_ref == "main"
    assert (dest / "README.md").read_text() == "hi"
