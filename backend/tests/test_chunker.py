"""The line-reconstruction test here is the one that matters most in the
whole ingestion pipeline: every citation the app will ever show rests on
start_line/end_line being exactly right (planning/decisions.md ADR-003)."""

from __future__ import annotations

from app.ingestion.chunker import chunk_text

PYTHON_SOURCE = '''"""Module docstring."""


def add(a, b):
    """Add two numbers."""
    return a + b


def subtract(a, b):
    """Subtract b from a."""
    return a - b


class Calculator:
    """A tiny calculator."""

    def multiply(self, a, b):
        return a * b

    def divide(self, a, b):
        if b == 0:
            raise ValueError("division by zero")
        return a / b
'''


def test_chunk_text_line_numbers_reconstruct_exactly():
    chunks = chunk_text(PYTHON_SOURCE, path="calc.py", doc_type="code", language="python")
    assert len(chunks) >= 1

    lines = PYTHON_SOURCE.splitlines()
    for chunk in chunks:
        reconstructed = "\n".join(lines[chunk.start_line - 1 : chunk.end_line])
        assert reconstructed == chunk.body, (
            f"chunk {chunk.chunk_index} (lines {chunk.start_line}-{chunk.end_line}) "
            "does not match the source at those line numbers"
        )


def test_chunk_text_indices_and_order():
    chunks = chunk_text(PYTHON_SOURCE, path="calc.py", doc_type="code", language="python")
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    starts = [c.start_line for c in chunks]
    assert starts == sorted(starts)


def test_chunk_text_extracts_symbols():
    chunks = chunk_text(PYTHON_SOURCE, path="calc.py", doc_type="code", language="python")
    all_symbols = {s for c in chunks for s in c.symbols}
    assert {"add", "subtract", "Calculator"} <= all_symbols


def test_chunk_text_embed_text_has_header_and_verbatim_body():
    chunks = chunk_text(PYTHON_SOURCE, path="calc.py", doc_type="code", language="python")
    first = chunks[0]
    assert first.embed_text.startswith("# File: calc.py (lines")
    assert first.body in first.embed_text


def test_chunk_text_empty_or_blank_input_returns_no_chunks():
    assert chunk_text("", path="empty.py", doc_type="code", language="python") == []
    assert chunk_text("   \n  \n", path="empty.py", doc_type="code", language="python") == []


def test_chunk_text_falls_back_and_still_reconstructs_for_unmapped_language():
    text = "\n".join(f"SELECT {i} FROM users;" for i in range(200))
    chunks = chunk_text(text, path="query.sql", doc_type="code", language="sql")
    assert len(chunks) >= 1

    lines = text.splitlines()
    for chunk in chunks:
        reconstructed = "\n".join(lines[chunk.start_line - 1 : chunk.end_line])
        assert reconstructed == chunk.body


def test_chunk_text_content_sha_is_stable_and_distinguishes_chunks():
    chunks = chunk_text(PYTHON_SOURCE, path="calc.py", doc_type="code", language="python")
    shas = [c.content_sha for c in chunks]
    assert len(shas) == len(set(shas)), "distinct chunks should not collide on content_sha"

    again = chunk_text(PYTHON_SOURCE, path="calc.py", doc_type="code", language="python")
    assert [c.content_sha for c in chunks] == [c.content_sha for c in again]


def test_chunk_id_includes_path_and_index():
    chunks = chunk_text(PYTHON_SOURCE, path="calc.py", doc_type="code", language="python")
    assert chunks[0].chunk_id() == "calc.py::0"
