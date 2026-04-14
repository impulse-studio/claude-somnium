"""Tests for AST-aware code chunking via tree-sitter."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from somnium.code.ast_chunker import _LANG_MAP, ast_chunk_source_file
from somnium.code.chunker import chunk_source_file

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def test_python_function_boundaries(tmp_path: Path) -> None:
    """Two functions → two separate chunks, each containing the right function."""
    src = dedent("""\
        def foo():
            return 1

        def bar():
            return 2
    """)
    p = tmp_path / "two_funcs.py"
    p.write_text(src)
    result = ast_chunk_source_file(p, chunk_lines=40)
    assert result is not None
    digest, chunks = result
    assert digest
    # Should have at least 2 chunks: one per function (+ possibly a gap chunk)
    func_chunks = [c for c in chunks if "def " in c.text]
    assert len(func_chunks) == 2
    assert "def foo" in func_chunks[0].text
    assert "def bar" in func_chunks[1].text


def test_python_class_single_chunk(tmp_path: Path) -> None:
    """Small class fits in one chunk."""
    src = dedent("""\
        class Greeter:
            def __init__(self, name):
                self.name = name

            def greet(self):
                return f"Hello, {self.name}"
    """)
    p = tmp_path / "cls.py"
    p.write_text(src)
    result = ast_chunk_source_file(p, chunk_lines=40)
    assert result is not None
    _, chunks = result
    class_chunks = [c for c in chunks if "class Greeter" in c.text]
    assert len(class_chunks) == 1
    assert "def greet" in class_chunks[0].text


def test_python_large_function_splits(tmp_path: Path) -> None:
    """A 200-line function gets split into sub-chunks with overlap."""
    lines = ["def big_func():"]
    for i in range(200):
        lines.append(f"    x_{i} = {i}")
    p = tmp_path / "big.py"
    p.write_text("\n".join(lines) + "\n")
    result = ast_chunk_source_file(p, chunk_lines=40, overlap_ratio=0.25)
    assert result is not None
    _, chunks = result
    # 201 lines / ~30 step = ~7 sub-chunks
    assert len(chunks) >= 5
    # First chunk starts at line 1
    assert chunks[0].start_line == 1
    assert "def big_func" in chunks[0].text


def test_python_preamble_chunk(tmp_path: Path) -> None:
    """Imports and module constants before functions get their own chunk."""
    src = dedent("""\
        import os
        import sys

        CONSTANT = 42

        def main():
            print(CONSTANT)
    """)
    p = tmp_path / "preamble.py"
    p.write_text(src)
    result = ast_chunk_source_file(p, chunk_lines=40)
    assert result is not None
    _, chunks = result
    # Should have at least 2 chunks: preamble + function
    assert len(chunks) >= 2
    assert "import os" in chunks[0].text
    assert "def main" in chunks[-1].text


def test_python_decorated_function(tmp_path: Path) -> None:
    """Decorated functions are captured as single chunks."""
    src = dedent("""\
        import functools

        @functools.cache
        def expensive(n):
            return sum(range(n))
    """)
    p = tmp_path / "deco.py"
    p.write_text(src)
    result = ast_chunk_source_file(p, chunk_lines=40)
    assert result is not None
    _, chunks = result
    deco_chunks = [c for c in chunks if "@functools.cache" in c.text]
    assert len(deco_chunks) == 1
    assert "def expensive" in deco_chunks[0].text


# ---------------------------------------------------------------------------
# JavaScript / TypeScript
# ---------------------------------------------------------------------------


def test_js_function_declaration(tmp_path: Path) -> None:
    src = dedent("""\
        function greet(name) {
            return "Hello, " + name;
        }

        function farewell(name) {
            return "Bye, " + name;
        }
    """)
    p = tmp_path / "funcs.js"
    p.write_text(src)
    result = ast_chunk_source_file(p, chunk_lines=40)
    assert result is not None
    _, chunks = result
    func_chunks = [c for c in chunks if "function " in c.text]
    assert len(func_chunks) == 2


def test_ts_class_declaration(tmp_path: Path) -> None:
    src = dedent("""\
        class Animal {
            name: string;
            constructor(name: string) {
                this.name = name;
            }
            speak(): string {
                return this.name + " speaks";
            }
        }
    """)
    p = tmp_path / "animal.ts"
    p.write_text(src)
    result = ast_chunk_source_file(p, chunk_lines=40)
    assert result is not None
    _, chunks = result
    class_chunks = [c for c in chunks if "class Animal" in c.text]
    assert len(class_chunks) == 1


# ---------------------------------------------------------------------------
# Go
# ---------------------------------------------------------------------------


def test_go_function_and_method(tmp_path: Path) -> None:
    src = dedent("""\
        package main

        func Add(a, b int) int {
            return a + b
        }

        func Multiply(a, b int) int {
            return a * b
        }
    """)
    p = tmp_path / "math.go"
    p.write_text(src)
    result = ast_chunk_source_file(p, chunk_lines=40)
    assert result is not None
    _, chunks = result
    func_chunks = [c for c in chunks if "func " in c.text]
    assert len(func_chunks) == 2


# ---------------------------------------------------------------------------
# Rust
# ---------------------------------------------------------------------------


def test_rust_impl_block(tmp_path: Path) -> None:
    src = dedent("""\
        struct Point {
            x: f64,
            y: f64,
        }

        impl Point {
            fn new(x: f64, y: f64) -> Self {
                Point { x, y }
            }
            fn distance(&self) -> f64 {
                (self.x * self.x + self.y * self.y).sqrt()
            }
        }
    """)
    p = tmp_path / "point.rs"
    p.write_text(src)
    result = ast_chunk_source_file(p, chunk_lines=40)
    assert result is not None
    _, chunks = result
    impl_chunks = [c for c in chunks if "impl Point" in c.text]
    assert len(impl_chunks) == 1


# ---------------------------------------------------------------------------
# Fallback / edge cases
# ---------------------------------------------------------------------------


def test_unsupported_extension_returns_none(tmp_path: Path) -> None:
    """Files with unmapped extensions return None (caller falls back)."""
    p = tmp_path / "readme.txt"
    p.write_text("Hello world\n")
    assert ast_chunk_source_file(p) is None


def test_parse_failure_returns_none(tmp_path: Path) -> None:
    """Completely garbled content should still return something or None gracefully."""
    p = tmp_path / "bad.py"
    p.write_text("\x00\x01\x02\x03" * 100)
    # Should not raise — returns None or valid chunks
    result = ast_chunk_source_file(p)
    # Either None (fallback) or empty chunks is fine
    assert result is None or (isinstance(result, tuple) and isinstance(result[1], list))


def test_empty_file_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "empty.py"
    p.write_text("")
    assert ast_chunk_source_file(p) is None


def test_no_matching_nodes_returns_none(tmp_path: Path) -> None:
    """A file with only imports (no functions/classes) returns None for fallback."""
    src = dedent("""\
        import os
        import sys
        x = 1
    """)
    p = tmp_path / "just_imports.py"
    p.write_text(src)
    # No function_definition or class_definition nodes
    assert ast_chunk_source_file(p) is None


def test_fallback_integration(tmp_path: Path) -> None:
    """chunk_source_file on a .txt file still uses fixed-line fallback."""
    p = tmp_path / "data.txt"
    lines = [f"line {i}" for i in range(50)]
    p.write_text("\n".join(lines) + "\n")
    # .txt not in _LANG_MAP → AST returns None → fixed-line kicks in
    assert ".txt" not in _LANG_MAP
    digest, chunks = chunk_source_file(p, chunk_lines=20)
    assert digest
    assert len(chunks) >= 3


def test_ast_produces_valid_codechunks(tmp_path: Path) -> None:
    """All chunks from AST chunker have correct fields."""
    src = dedent("""\
        def alpha():
            pass

        def beta():
            pass
    """)
    p = tmp_path / "valid.py"
    p.write_text(src)
    result = ast_chunk_source_file(p)
    assert result is not None
    digest, chunks = result
    for c in chunks:
        assert c.file_path == p
        assert c.file_hash == digest
        assert c.start_line >= 1
        assert c.end_line >= c.start_line
        assert c.language == "py"
        assert c.text.strip()


def test_gap_between_functions(tmp_path: Path) -> None:
    """Lines between functions (comments, blank lines) get chunked too."""
    src = dedent("""\
        def first():
            return 1

        # This is a comment between functions
        GLOBAL_VAR = 42

        def second():
            return 2
    """)
    p = tmp_path / "gaps.py"
    p.write_text(src)
    result = ast_chunk_source_file(p, chunk_lines=40)
    assert result is not None
    _, chunks = result
    all_text = "\n".join(c.text for c in chunks)
    assert "GLOBAL_VAR" in all_text
    assert "def first" in all_text
    assert "def second" in all_text


def test_chunk_lines_respected(tmp_path: Path) -> None:
    """No chunk exceeds chunk_lines in line count."""
    lines = ["def big():"]
    for i in range(150):
        lines.append(f"    x_{i} = {i}")
    p = tmp_path / "limited.py"
    p.write_text("\n".join(lines) + "\n")
    result = ast_chunk_source_file(p, chunk_lines=30)
    assert result is not None
    _, chunks = result
    for c in chunks:
        line_count = c.end_line - c.start_line + 1
        assert line_count <= 30
