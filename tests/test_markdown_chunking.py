"""Tests for markdown parsing and chunking."""

from __future__ import annotations

from pathlib import Path

from somnium.storage.markdown import chunk_file, file_hash


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_chunks_simple_file(tmp_path: Path) -> None:
    path = tmp_path / "simple.md"
    _write(
        path,
        "---\ntitle: Simple\ntags: [test]\n---\n\n"
        "# Hello\n\nThis is the first paragraph.\n\n"
        "## Subsection\n\nSecond paragraph goes here with enough body "
        "to satisfy the minimum chunk length threshold for testing.\n",
    )
    digest, chunks = chunk_file(path)
    assert digest == file_hash(path)
    assert len(chunks) >= 1
    # Frontmatter flows through
    assert chunks[0].frontmatter_data.get("title") == "Simple"
    # Heading path captured
    assert any(ch.heading_path for ch in chunks)


def test_chunks_noheading_file(tmp_path: Path) -> None:
    path = tmp_path / "noheading.md"
    _write(path, "Just a bunch of free text without any heading whatsoever.")
    digest, chunks = chunk_file(path)
    assert len(chunks) == 1
    assert chunks[0].heading_path == []
    assert "free text" in chunks[0].text


def test_chunks_oversized_section(tmp_path: Path) -> None:
    path = tmp_path / "big.md"
    body = "# Big\n\n" + ("\n\n".join([f"Paragraph {i} " * 50 for i in range(10)]))
    _write(path, body)
    _, chunks = chunk_file(path)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c.text) <= 2000  # respects chunk cap with slack


def test_display_text_adds_breadcrumb(tmp_path: Path) -> None:
    path = tmp_path / "deep.md"
    _write(
        path,
        "# A\n\n## B\n\n### C\n\n"
        "Deeply nested content with enough length for chunking and retrieval.\n",
    )
    _, chunks = chunk_file(path)
    c = chunks[0]
    assert c.heading_path == ["A", "B", "C"]
    assert "A > B > C" in c.display_text


def test_hash_changes_with_content(tmp_path: Path) -> None:
    path = tmp_path / "h.md"
    _write(path, "first version of the file")
    h1 = file_hash(path)
    _write(path, "second version of the file")
    h2 = file_hash(path)
    assert h1 != h2
