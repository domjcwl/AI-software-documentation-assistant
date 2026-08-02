"""Split a scanned file into line-attributed, citation-ready chunks.

See planning/decisions.md ADR-003 (language-aware splitting + computed
line numbers) and ADR-004 (embedded text != stored text). The Language
enum members used below were verified against the installed
langchain-text-splitters==1.1.2, not assumed from memory — it has no
mapping for several languages we index (shell, sql, json, yaml, dart,
objective-c, ...), which fall back to the plain recursive splitter.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from app.ingestion.scanner import ScannedFile

_CODE_CHUNK_KWARGS = {"chunk_size": 1200, "chunk_overlap": 150}
_DOC_CHUNK_KWARGS = {"chunk_size": 1500, "chunk_overlap": 200}

_LANGUAGE_MAP: dict[str, Language] = {
    "python": Language.PYTHON,
    "javascript": Language.JS,
    "typescript": Language.TS,
    "java": Language.JAVA,
    "go": Language.GO,
    "rust": Language.RUST,
    "ruby": Language.RUBY,
    "php": Language.PHP,
    "csharp": Language.CSHARP,
    "cpp": Language.CPP,
    "c": Language.C,
    "kotlin": Language.KOTLIN,
    "swift": Language.SWIFT,
    "scala": Language.SCALA,
    "perl": Language.PERL,
    "lua": Language.LUA,
    "markdown": Language.MARKDOWN,
    "rst": Language.RST,
}

# Crude, deliberately language-agnostic symbol extraction (planning/decisions.md
# ADR-004): applying every pattern to every chunk regardless of language
# occasionally over-matches, but a stray extra token in the embedding header
# is harmless noise, not a correctness bug — and this needs no real parser.
_SYMBOL_PATTERNS = [
    re.compile(r"^\s*(?:async\s+)?def\s+(\w+)", re.MULTILINE),
    re.compile(r"^\s*class\s+(\w+)", re.MULTILINE),
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?function\s*\*?\s+(\w+)", re.MULTILINE),
    re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:\(|async)", re.MULTILINE),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)", re.MULTILINE),
    re.compile(r"^\s*(?:pub\s+)?fn\s+(\w+)", re.MULTILINE),
    re.compile(r"^\s*(?:public|private|protected)\s+(?:static\s+)?[\w<>\[\],\s]+?\s(\w+)\s*\(", re.MULTILINE),
    re.compile(r"^\s*type\s+(\w+)", re.MULTILINE),
]
_MAX_SYMBOLS = 8


@dataclass
class Chunk:
    path: str
    doc_type: str
    language: str
    chunk_index: int
    start_line: int
    end_line: int
    symbols: list[str] = field(default_factory=list)
    body: str = ""  # verbatim source — what citations and snippets show
    embed_text: str = ""  # body + synthetic header — what actually gets embedded
    content_sha: str = ""

    def chunk_id(self) -> str:
        return f"{self.path}::{self.chunk_index}"

    def metadata_dict(self, project_id: str) -> dict:
        """Chroma metadata values must be str/int/float/bool — symbols is
        flattened to a joined string here rather than stored as a list."""
        return {
            "project_id": project_id,
            "path": self.path,
            "doc_type": self.doc_type,
            "language": self.language,
            "chunk_index": self.chunk_index,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "symbols": ", ".join(self.symbols),
            "sha": self.content_sha,
        }


def chunk_file(scanned: ScannedFile) -> list[Chunk]:
    """Read, split, and line-attribute a single scanned file. Returns []
    for empty or undecodable files rather than raising — one bad file
    should not fail the whole indexing job."""
    text = read_source_text(scanned.abs_path)
    if text is None or not text.strip():
        return []
    return chunk_text(text, path=scanned.path, doc_type=scanned.doc_type, language=scanned.language)


def chunk_text(text: str, *, path: str, doc_type: str, language: str) -> list[Chunk]:
    """Split already-loaded text. Used for real files (via chunk_file) and
    for the synthetic manifest documents built in app.ingestion.manifest,
    which have no file on disk to read."""
    splitter = _get_splitter(doc_type, language)
    docs = splitter.create_documents([text])

    chunks: list[Chunk] = []
    for idx, doc in enumerate(docs):
        body = doc.page_content
        if not body.strip():
            continue
        start_index = doc.metadata.get("start_index", 0)
        start_line = text.count("\n", 0, start_index) + 1
        end_line = start_line + body.count("\n")
        symbols = _extract_symbols(body)

        header = f"# File: {path} (lines {start_line}-{end_line}) [{language}]"
        if symbols:
            header += f"\n# Symbols: {', '.join(symbols)}"
        embed_text = f"{header}\n{body}"
        content_sha = hashlib.sha1(body.encode("utf-8", errors="ignore")).hexdigest()[:12]

        chunks.append(
            Chunk(
                path=path,
                doc_type=doc_type,
                language=language,
                chunk_index=idx,
                start_line=start_line,
                end_line=end_line,
                symbols=symbols,
                body=body,
                embed_text=embed_text,
                content_sha=content_sha,
            )
        )
    return chunks


def _get_splitter(doc_type: str, language: str) -> RecursiveCharacterTextSplitter:
    sizing = _CODE_CHUNK_KWARGS if doc_type == "code" else _DOC_CHUNK_KWARGS
    lc_language = _LANGUAGE_MAP.get(language)
    if lc_language is not None:
        return RecursiveCharacterTextSplitter.from_language(lc_language, add_start_index=True, **sizing)
    return RecursiveCharacterTextSplitter(add_start_index=True, **sizing)


def _extract_symbols(body: str, limit: int = _MAX_SYMBOLS) -> list[str]:
    found: list[str] = []
    for pattern in _SYMBOL_PATTERNS:
        for match in pattern.finditer(body):
            name = match.group(1)
            if name and name not in found:
                found.append(name)
                if len(found) >= limit:
                    return found
    return found


def read_source_text(path) -> str | None:
    """UTF-8 with a latin-1 fallback. Public so app.ingestion.pipeline can
    reuse the same read when it needs line counts for the directory tree,
    rather than reading each file a second time."""
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("latin-1")
        except UnicodeDecodeError:
            return None
