from __future__ import annotations

import pytest

from app.errors import InvalidRepoUrlError
from app.services.repo_url import parse_repo_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/tiangolo/fastapi", ("tiangolo", "fastapi", None)),
        ("https://github.com/tiangolo/fastapi/tree/master", ("tiangolo", "fastapi", "master")),
        ("https://github.com/tiangolo/fastapi.git", ("tiangolo", "fastapi", None)),
        ("tiangolo/fastapi", ("tiangolo", "fastapi", None)),
        ("github.com/tiangolo/fastapi", ("tiangolo", "fastapi", None)),
        ("https://GitHub.com/tiangolo/fastapi", ("tiangolo", "fastapi", None)),
        ("https://github.com/tiangolo/fastapi/", ("tiangolo", "fastapi", None)),
    ],
)
def test_parse_repo_url_accepts_valid_forms(url, expected):
    assert parse_repo_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "not a url",
        "https://gitlab.com/owner/repo",
        "https://github.com/owner",
        "https://github.com/owner/repo/pulls/5",
        "",
        "   ",
    ],
)
def test_parse_repo_url_rejects_invalid_forms(url):
    with pytest.raises(InvalidRepoUrlError):
        parse_repo_url(url)
