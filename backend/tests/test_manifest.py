from __future__ import annotations

from pathlib import Path

from app.ingestion.manifest import build_manifests
from app.ingestion.scanner import ScannedFile


def _sf(path: str, doc_type: str, language: str) -> ScannedFile:
    return ScannedFile(path=path, abs_path=Path(path), doc_type=doc_type, language=language, size_bytes=100)


def test_build_manifests_tree_counts_and_index():
    files = [_sf("main.py", "code", "python"), _sf("src/utils.py", "code", "python"), _sf("README.md", "doc", "markdown")]
    line_counts = {"main.py": 10, "src/utils.py": 5, "README.md": 3}
    symbols_by_path = {"main.py": ["main"], "src/utils.py": ["add", "Helper"]}

    docs = build_manifests(files, line_counts, symbols_by_path, truncated=False)

    tree = docs.directory_tree_json
    assert tree["file_count"] == 3
    assert tree["total_lines"] == 18
    names = {c["name"] for c in tree["children"]}
    assert {"src", "main.py", "README.md"} <= names

    src_dir = next(c for c in tree["children"] if c["name"] == "src")
    assert src_dir["type"] == "dir"
    assert src_dir["file_count"] == 1

    assert "main.py" in docs.file_index_text
    assert "add" in docs.file_index_text  # per-file symbols surfaced
    assert "python" in docs.repo_summary_text.lower()


def test_build_manifests_truncated_note_appears_in_tree_text():
    docs = build_manifests([_sf("a.py", "code", "python")], {"a.py": 1}, {}, truncated=True)
    assert "NOTE" in docs.directory_tree_text


def test_build_manifests_not_truncated_has_no_note():
    docs = build_manifests([_sf("a.py", "code", "python")], {"a.py": 1}, {}, truncated=False)
    assert "NOTE" not in docs.directory_tree_text


def test_build_manifests_detects_entry_points_and_project_signals():
    files = [
        _sf("main.py", "code", "python"),
        _sf("requirements.txt", "doc", "text"),
        _sf("Dockerfile", "config", "dockerfile"),
        _sf(".github/workflows/ci.yml", "config", "yaml"),
    ]
    line_counts = {f.path: 1 for f in files}
    docs = build_manifests(files, line_counts, {}, truncated=False)

    assert "main.py" in docs.repo_summary_text
    assert "requirements.txt" in docs.repo_summary_text
    assert "Dockerfile present: True" in docs.repo_summary_text
    assert "CI configuration present: True" in docs.repo_summary_text


def test_build_manifests_on_no_files():
    docs = build_manifests([], {}, {}, truncated=False)
    assert docs.directory_tree_json["file_count"] == 0
    assert "No code files detected" in docs.repo_summary_text
