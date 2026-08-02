"""Synthetic documents built from the whole scan, not from any single file:
a directory tree, a repo-level summary, and a file index. See
planning/architecture.md §3.4.

These get embedded like any other document so retrieval can surface them
for "summarize the architecture"-style questions. The JSON tree is also
persisted verbatim by the pipeline so a future `directory` question can
read it directly instead of through vector search — retrieval is
approximate, and we already have the exact answer at index time
(planning/decisions.md ADR-006).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.ingestion.scanner import ScannedFile

_ENTRY_POINT_NAMES = {
    "main.py", "app.py", "manage.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "server.js", "server.ts", "main.go", "main.rs",
}
_DEPENDENCY_MANIFEST_NAMES = {
    "requirements.txt", "pyproject.toml", "package.json", "go.mod", "cargo.toml",
    "gemfile", "composer.json", "pom.xml", "build.gradle",
}
_CI_MARKER_DIRS = (".github/workflows/",)
_CI_MARKER_FILES = {".gitlab-ci.yml", ".travis.yml", "jenkinsfile", "azure-pipelines.yml"}
_README_NAMES = {"readme", "readme.md", "readme.rst", "readme.txt"}

_MAX_TREE_LINES = 400
_README_EXCERPT_LINES = 200
_SYMBOLS_PER_FILE = 12


@dataclass
class ManifestDocs:
    directory_tree_text: str
    directory_tree_json: dict
    repo_summary_text: str
    file_index_text: str


def build_manifests(
    files: list[ScannedFile],
    line_counts: dict[str, int],
    symbols_by_path: dict[str, list[str]],
    truncated: bool,
) -> ManifestDocs:
    tree_json = _build_tree_json(files, line_counts)
    return ManifestDocs(
        directory_tree_text=_render_tree_text(tree_json, truncated),
        directory_tree_json=tree_json,
        repo_summary_text=_build_repo_summary(files, line_counts),
        file_index_text=_build_file_index(files, line_counts, symbols_by_path),
    )


def _build_tree_json(files: list[ScannedFile], line_counts: dict[str, int]) -> dict:
    root: dict = {"name": "", "type": "dir", "children": {}}
    for f in files:
        parts = f.path.split("/")
        node = root
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                node["children"].setdefault(
                    part,
                    {
                        "name": part,
                        "type": "file",
                        "path": f.path,
                        "language": f.language,
                        "doc_type": f.doc_type,
                        "lines": line_counts.get(f.path, 0),
                    },
                )
            else:
                node = node["children"].setdefault(
                    part, {"name": part, "type": "dir", "children": {}}
                )
    _finalize_dir_counts(root)
    return _children_dict_to_sorted_list(root)


def _finalize_dir_counts(node: dict) -> tuple[int, int]:
    if node["type"] == "file":
        return 1, node["lines"]
    file_count = total_lines = 0
    for child in node["children"].values():
        fc, tl = _finalize_dir_counts(child)
        file_count += fc
        total_lines += tl
    node["file_count"] = file_count
    node["total_lines"] = total_lines
    return file_count, total_lines


def _children_dict_to_sorted_list(node: dict) -> dict:
    if node["type"] == "file":
        return node
    ordered = sorted(node["children"].values(), key=lambda c: (c["type"] != "dir", c["name"].lower()))
    node["children"] = [_children_dict_to_sorted_list(c) for c in ordered]
    return node


def _render_tree_text(tree: dict, truncated: bool) -> str:
    lines: list[str] = []
    if truncated:
        lines.append(
            "NOTE: this repository has more files than the indexing limit; "
            "the tree below reflects only the files that were indexed."
        )

    def walk(node: dict, prefix: str) -> None:
        for child in node.get("children", []):
            if child["type"] == "dir":
                lines.append(f"{prefix}{child['name']}/  ({child['file_count']} files, {child['total_lines']} lines)")
                walk(child, prefix + "  ")
            else:
                lines.append(f"{prefix}{child['name']}  ({child.get('lines', 0)} lines)")

    walk(tree, "")

    if len(lines) > _MAX_TREE_LINES:
        omitted = len(lines) - _MAX_TREE_LINES
        lines = lines[:_MAX_TREE_LINES] + [f"... ({omitted} more entries omitted)"]
    return "\n".join(lines)


def _build_repo_summary(files: list[ScannedFile], line_counts: dict[str, int]) -> str:
    lang_lines: dict[str, int] = {}
    for f in files:
        if f.doc_type == "code":
            lang_lines[f.language] = lang_lines.get(f.language, 0) + line_counts.get(f.path, 0)
    total = sum(lang_lines.values()) or 1
    lang_share = sorted(lang_lines.items(), key=lambda kv: -kv[1])

    entry_points = sorted(f.path for f in files if Path(f.path).name.lower() in _ENTRY_POINT_NAMES)
    dependency_manifests = sorted(
        f.path for f in files if Path(f.path).name.lower() in _DEPENDENCY_MANIFEST_NAMES
    )
    has_dockerfile = any(Path(f.path).name.lower() == "dockerfile" for f in files)
    has_ci = any(
        f.path.lower().startswith(_CI_MARKER_DIRS) or Path(f.path).name.lower() in _CI_MARKER_FILES
        for f in files
    )
    has_tests = any("test" in f.path.lower() for f in files)
    readme_excerpt = _read_readme_excerpt(files)

    lines = ["# Repository Summary", "", "## Languages (by lines of code)"]
    if lang_share:
        for lang, count in lang_share[:10]:
            lines.append(f"- {lang}: {count} lines ({100 * count / total:.0f}%)")
    else:
        lines.append("- No code files detected.")

    lines += ["", "## Entry points"]
    lines += [f"- {p}" for p in entry_points] if entry_points else ["- None detected by filename heuristic."]

    lines += ["", "## Dependency manifests"]
    lines += [f"- {p}" for p in dependency_manifests] if dependency_manifests else ["- None detected."]

    lines += [
        "",
        "## Project signals",
        f"- Dockerfile present: {has_dockerfile}",
        f"- CI configuration present: {has_ci}",
        f"- Test files present (heuristic: 'test' in path): {has_tests}",
        f"- Total indexed files: {len(files)}",
    ]

    if readme_excerpt:
        lines += ["", f"## README (first {_README_EXCERPT_LINES} lines)", "", readme_excerpt]

    return "\n".join(lines)


def _read_readme_excerpt(files: list[ScannedFile]) -> str:
    readme = next((f for f in files if Path(f.path).name.lower() in _README_NAMES), None)
    if readme is None:
        return ""
    try:
        text = readme.abs_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[:_README_EXCERPT_LINES])


def _build_file_index(
    files: list[ScannedFile], line_counts: dict[str, int], symbols_by_path: dict[str, list[str]]
) -> str:
    lines = ["# File Index", ""]
    for f in sorted(files, key=lambda x: x.path):
        entry = f"- {f.path} [{f.language}, {line_counts.get(f.path, 0)} lines]"
        symbols = symbols_by_path.get(f.path)
        if symbols:
            entry += f" — symbols: {', '.join(symbols[:_SYMBOLS_PER_FILE])}"
        lines.append(entry)
    return "\n".join(lines)
