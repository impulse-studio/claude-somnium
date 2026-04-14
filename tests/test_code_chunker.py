"""Tests for the code chunker and walker."""

from __future__ import annotations

from pathlib import Path

from somnium.code.chunker import chunk_source_file, file_hash
from somnium.code.walker import walk_code


def test_chunk_small_file(tmp_path: Path) -> None:
    p = tmp_path / "a.py"
    p.write_text("def foo():\n    return 1\n\nprint(foo())\n", encoding="utf-8")
    digest, chunks = chunk_source_file(p, chunk_lines=40)
    assert digest == file_hash(p)
    assert len(chunks) >= 1  # AST-aware: function + trailing gap
    assert chunks[0].language == "py"
    assert chunks[0].start_line == 1
    assert "def foo" in chunks[0].text


def test_chunk_oversized_file_splits_with_overlap(tmp_path: Path) -> None:
    p = tmp_path / "big.py"
    lines = [f"line_{i} = {i}" for i in range(100)]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _, chunks = chunk_source_file(p, chunk_lines=20, overlap_ratio=0.25)
    assert len(chunks) >= 5
    # Overlap: each chunk steps by 20 - 5 = 15 lines.
    for a, b in zip(chunks, chunks[1:], strict=False):
        assert b.start_line - a.start_line == 15 or b.start_line == a.start_line + 15


def test_chunk_skips_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "empty.py"
    p.write_text("", encoding="utf-8")
    digest, chunks = chunk_source_file(p, chunk_lines=40)
    assert chunks == []


def test_chunk_skips_oversized_file(tmp_path: Path) -> None:
    p = tmp_path / "huge.py"
    p.write_text("x = 1\n" * 200_000, encoding="utf-8")  # > 500KB
    _, chunks = chunk_source_file(p, chunk_lines=40, max_file_bytes=500_000)
    assert chunks == []


def test_display_text_has_breadcrumb(tmp_path: Path) -> None:
    p = tmp_path / "q.js"
    p.write_text("const x = 1;\nconst y = 2;\n", encoding="utf-8")
    _, chunks = chunk_source_file(p, chunk_lines=40)
    display = chunks[0].display_text
    assert "q.js:" in display
    assert "[js]" in display


def test_walk_code_respects_extensions(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x=1")
    (tmp_path / "b.txt").write_text("not code")
    (tmp_path / "c.rs").write_text("fn main(){}")
    files = walk_code(tmp_path)
    names = {p.name for p in files}
    assert "a.py" in names
    assert "c.rs" in names
    assert "b.txt" not in names


def test_walk_code_skips_ignored_dirs(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x=1")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "b.js").write_text("x=1")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "bad.py").write_text("x=1")
    files = walk_code(tmp_path)
    names = {p.name for p in files}
    assert "a.py" in names
    assert "b.js" not in names
    assert "bad.py" not in names


def test_walk_code_custom_ignore(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("x=1")
    (tmp_path / "skipme").mkdir()
    (tmp_path / "skipme" / "nope.py").write_text("x=1")
    files = walk_code(tmp_path, ignore=["skipme"])
    names = {p.name for p in files}
    assert "keep.py" in names
    assert "nope.py" not in names


def test_walk_code_skips_dotfiles(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x=1")
    (tmp_path / ".env.py").write_text("x=1")  # dotfile skipped
    files = walk_code(tmp_path)
    names = {p.name for p in files}
    assert "a.py" in names
    assert ".env.py" not in names
