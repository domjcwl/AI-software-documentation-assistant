from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import Settings
from app.ingestion.scanner import scan_repo

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "mini_repo"


@pytest.fixture()
def repo(tmp_path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE_DIR, dest)
    # A file whose extension is whitelisted but whose content is binary —
    # exercises the NUL-byte sniff independently of extension filtering.
    (dest / "data").mkdir(exist_ok=True)
    (dest / "data" / "corrupt.py").write_bytes(b"garbage\x00binary\x00data")
    return dest


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_scan_repo_keeps_expected_files(repo):
    result = scan_repo(repo, _settings())
    paths = {f.path for f in result.files}

    assert "main.py" in paths
    assert "README.md" in paths
    assert "src/utils.py" in paths

    assert not any(p.startswith("node_modules/") for p in paths), "node_modules/ must be pruned"
    assert not any(p.startswith(".git/") for p in paths), ".git/ must be pruned"
    assert "package-lock.json" not in paths, "lockfiles are rejected by name regardless of extension"
    assert "data/corrupt.py" not in paths, "binary content must be rejected despite a whitelisted extension"

    assert result.truncated is False
    assert result.files_seen == len(result.files)


def test_scan_repo_classifies_doc_types(repo):
    result = scan_repo(repo, _settings())
    by_path = {f.path: f for f in result.files}

    assert by_path["main.py"].doc_type == "code"
    assert by_path["main.py"].language == "python"
    assert by_path["README.md"].doc_type == "doc"


def test_scan_repo_respects_max_files(repo):
    result = scan_repo(repo, _settings(max_files=1))
    assert result.truncated is True
    assert len(result.files) == 1
    assert result.files_seen > 1


def test_scan_repo_respects_max_file_bytes(repo):
    result = scan_repo(repo, _settings(max_file_bytes=5))
    paths = {f.path for f in result.files}
    assert "main.py" not in paths  # every fixture file is bigger than 5 bytes
    assert len(result.files) == 0


def test_scan_repo_on_empty_dir_returns_no_files(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = scan_repo(empty, _settings())
    assert result.files == []
    assert result.truncated is False
