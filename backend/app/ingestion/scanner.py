"""Recursively scan an extracted repo and decide which files are worth
indexing. See planning/architecture.md §3.2.

Deliberate deviation from the original design note: classification here
is a *whitelist* of known code/doc/config extensions (app.ingestion.scanner
._CODE_EXTENSIONS etc.) rather than a binary-extension blacklist — a
whitelist can't miss an exotic binary format the way a blacklist can, so
the separate "binary extension" rejection step was dropped as redundant.
Full UTF-8 decodability is also deferred to app.ingestion.chunker, which
reads each kept file's full content anyway — checking it here too would
mean reading every file twice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.config import Settings

# Matched against any path segment, anywhere in the tree.
_IGNORED_DIR_NAMES = {
    ".git", "node_modules", "dist", "build", "__pycache__", ".venv", "venv", "env",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vscode",
    "target", "vendor", "coverage", ".next", ".nuxt", "out", "site-packages", ".terraform",
}

_GENERATED_FILENAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "cargo.lock", "gemfile.lock", "composer.lock", "go.sum",
}
_GENERATED_SUFFIXES = (".min.js", ".min.css", ".map", ".pb.go", "_pb2.py", ".snap")

_CODE_EXTENSIONS = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".jsx": "javascript",
    ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".cs": "csharp", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".c": "c", ".h": "c", ".kt": "kotlin", ".swift": "swift", ".scala": "scala",
    ".sh": "shell", ".bash": "shell", ".sql": "sql", ".m": "objective-c",
    ".pl": "perl", ".lua": "lua", ".dart": "dart",
}
_DOC_EXTENSIONS = {".md": "markdown", ".rst": "rst", ".txt": "text", ".adoc": "asciidoc"}
_CONFIG_EXTENSIONS = {
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".ini": "ini", ".cfg": "ini",
}
_SPECIAL_CONFIG_FILENAMES = {"dockerfile", "makefile", "gemfile", "rakefile", "procfile"}
_SPECIAL_DOC_FILENAMES = {"license", "readme", "contributing", "changelog", "notice", "authors", "codeowners"}


@dataclass
class ScannedFile:
    path: str  # posix-style, relative to repo root
    abs_path: Path
    doc_type: str  # "code" | "doc" | "config"
    language: str
    size_bytes: int


@dataclass
class ScanResult:
    files: list[ScannedFile]
    files_seen: int  # indexable files found before any MAX_FILES truncation
    truncated: bool


def scan_repo(repo_root: Path, settings: Settings) -> ScanResult:
    candidates: list[ScannedFile] = []

    for abs_path in _walk_files(repo_root):
        rel_posix = abs_path.relative_to(repo_root).as_posix()

        classification = classify_file(abs_path.name)
        if classification is None:
            continue
        doc_type, language = classification

        if _is_generated_or_lockfile(abs_path.name, rel_posix):
            continue

        try:
            size = abs_path.stat().st_size
        except OSError:
            continue
        if size == 0 or size > settings.max_file_bytes:
            continue

        if _looks_binary(abs_path):
            continue

        candidates.append(ScannedFile(rel_posix, abs_path, doc_type, language, size))

    candidates.sort(key=lambda f: f.path)
    truncated = len(candidates) > settings.max_files
    kept = _rank_and_truncate(candidates, settings.max_files) if truncated else candidates
    kept.sort(key=lambda f: f.path)
    return ScanResult(files=kept, files_seen=len(candidates), truncated=truncated)


def classify_file(name: str) -> tuple[str, str] | None:
    """Return (doc_type, language) for a filename, or None if it's not a
    type we index at all — silently skipped, not counted as rejected."""
    stem_lower = name.lower()
    ext = PurePosixPath(name).suffix.lower()

    if stem_lower in _SPECIAL_CONFIG_FILENAMES:
        return "config", stem_lower
    if stem_lower in _SPECIAL_DOC_FILENAMES:
        return "doc", "text"
    if ext in _CODE_EXTENSIONS:
        return "code", _CODE_EXTENSIONS[ext]
    if ext in _DOC_EXTENSIONS:
        return "doc", _DOC_EXTENSIONS[ext]
    if ext in _CONFIG_EXTENSIONS:
        return "config", _CONFIG_EXTENSIONS[ext]
    return None


def _walk_files(repo_root: Path):
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIR_NAMES]
        for filename in filenames:
            yield Path(dirpath) / filename


def _is_generated_or_lockfile(filename: str, rel_posix: str) -> bool:
    if filename.lower() in _GENERATED_FILENAMES:
        return True
    lower = rel_posix.lower()
    return any(lower.endswith(suffix) for suffix in _GENERATED_SUFFIXES)


def _looks_binary(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
    except OSError:
        return True
    return b"\x00" in chunk


def _rank_key(f: ScannedFile) -> tuple[int, int, int]:
    """Docs/config first, then shallower paths, then larger source files —
    used only when MAX_FILES forces a truncation."""
    type_rank = 0 if f.doc_type in ("doc", "config") else 1
    depth = f.path.count("/")
    return (type_rank, depth, -f.size_bytes)


def _rank_and_truncate(files: list[ScannedFile], max_files: int) -> list[ScannedFile]:
    return sorted(files, key=_rank_key)[:max_files]
