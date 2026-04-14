"""AST-aware code chunking via tree-sitter.

Splits source files at function/class/method boundaries so each chunk is a
semantically coherent unit.  Falls back to ``None`` (caller should use
fixed-line chunking) when:

- The file extension is not mapped to a tree-sitter language.
- The tree-sitter parser is unavailable or fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .chunker import CodeChunk, file_hash

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Extension → tree-sitter language name
# ---------------------------------------------------------------------------

_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".lua": "lua",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hs": "haskell",
    ".dart": "dart",
    ".r": "r",
    ".jl": "julia",
    ".vue": "vue",
    ".svelte": "svelte",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
}

# ---------------------------------------------------------------------------
# Top-level node types to treat as chunk boundaries, per language
# ---------------------------------------------------------------------------

_CHUNK_NODE_TYPES: dict[str, set[str]] = {
    "python": {"function_definition", "class_definition", "decorated_definition"},
    "javascript": {
        "function_declaration",
        "class_declaration",
        "export_statement",
        "lexical_declaration",
    },
    "typescript": {
        "function_declaration",
        "class_declaration",
        "export_statement",
        "lexical_declaration",
        "interface_declaration",
        "type_alias_declaration",
    },
    "tsx": {
        "function_declaration",
        "class_declaration",
        "export_statement",
        "lexical_declaration",
        "interface_declaration",
        "type_alias_declaration",
    },
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "rust": {
        "function_item",
        "impl_item",
        "struct_item",
        "enum_item",
        "trait_item",
        "mod_item",
    },
    "java": {"class_declaration", "interface_declaration", "enum_declaration", "method_declaration"},
    "kotlin": {"function_declaration", "class_declaration", "object_declaration"},
    "scala": {"function_definition", "class_definition", "object_definition", "trait_definition"},
    "c": {"function_definition", "struct_specifier", "enum_specifier"},
    "cpp": {"function_definition", "class_specifier", "struct_specifier", "namespace_definition"},
    "c_sharp": {
        "class_declaration",
        "method_declaration",
        "interface_declaration",
        "struct_declaration",
    },
    "ruby": {"method", "class", "module", "singleton_method"},
    "php": {"function_definition", "class_declaration", "method_declaration"},
    "swift": {"function_declaration", "class_declaration", "struct_declaration", "enum_declaration"},
    "lua": {"function_declaration", "function_definition_statement"},
    "elixir": {"call"},  # def/defmodule are calls in Elixir's AST
    "haskell": {"function", "type_alias", "data_type"},
    "dart": {"function_signature", "class_definition", "method_signature"},
    "bash": {"function_definition"},
    "html": {"element"},
    "sql": {"create_table_statement", "create_function_statement", "select_statement"},
}

_DEFAULT_CHUNK_TYPES: set[str] = {
    "function_definition",
    "function_declaration",
    "class_definition",
    "class_declaration",
    "method_declaration",
    "method_definition",
}

# ---------------------------------------------------------------------------
# Maximum file size (same as fixed-line chunker)
# ---------------------------------------------------------------------------

_MAX_FILE_BYTES = 500_000


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ast_chunk_source_file(
    path: Path,
    *,
    chunk_lines: int = 40,
    overlap_ratio: float = 0.25,
) -> tuple[str, list[CodeChunk]] | None:
    """Try to chunk *path* using tree-sitter AST boundaries.

    Returns ``(file_hash, chunks)`` on success, or ``None`` when AST
    chunking is not available for this file (unsupported extension,
    missing grammar, parse failure).  The caller should fall back to
    fixed-line chunking when ``None`` is returned.
    """
    raw, ext, lang = _read_and_detect(path)
    if raw is None or lang is None:
        return None

    lines = raw.splitlines()
    if not lines:
        return None

    tree = _parse(lang, raw)
    if tree is None:
        return None

    node_types = _CHUNK_NODE_TYPES.get(lang, _DEFAULT_CHUNK_TYPES)
    spans = _collect_spans(tree.root_node, node_types)
    if not spans:
        return None

    digest = file_hash(path)
    ctx = _ChunkContext(
        path=path, digest=digest, language=ext.lstrip("."),
        chunk_lines=chunk_lines, overlap_ratio=overlap_ratio,
    )
    chunks = _spans_to_chunks(spans, lines, ctx)
    return (digest, chunks) if chunks else None


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _ChunkContext:
    path: Path
    digest: str
    language: str
    chunk_lines: int
    overlap_ratio: float


def _read_and_detect(path: Path) -> tuple[str | None, str, str | None]:
    """Read *path* and detect its tree-sitter language.

    Returns ``(source, extension, language)`` — any of which may be ``None``
    to signal that AST chunking should be skipped.
    """
    if not path.exists() or not path.is_file():
        return None, "", None
    try:
        size = path.stat().st_size
    except OSError:
        return None, "", None
    if size > _MAX_FILE_BYTES or size == 0:
        return None, "", None

    ext = path.suffix.lower()
    lang = _LANG_MAP.get(ext)
    if lang is None:
        return None, ext, None

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None, ext, None

    return raw, ext, lang


def _get_language_mod(lang: str) -> object | None:
    """Import the tree-sitter language module for *lang*, or None if unavailable."""
    import_map: dict[str, str] = {
        "python": "tree_sitter_python",
        "javascript": "tree_sitter_javascript",
        "typescript": "tree_sitter_typescript",
        "tsx": "tree_sitter_typescript",
        "go": "tree_sitter_go",
        "rust": "tree_sitter_rust",
        "java": "tree_sitter_java",
        "ruby": "tree_sitter_ruby",
        "c": "tree_sitter_c",
        "cpp": "tree_sitter_cpp",
        "c_sharp": "tree_sitter_c_sharp",
        "php": "tree_sitter_php",
        "bash": "tree_sitter_bash",
        "html": "tree_sitter_html",
        "css": "tree_sitter_css",
    }
    mod_name = import_map.get(lang)
    if mod_name is None:
        return None
    try:
        import importlib
        return importlib.import_module(mod_name)
    except ImportError:
        return None


def _parse(lang: str, source: str) -> object | None:
    """Parse *source* with tree-sitter, returning the Tree or None on failure."""
    mod = _get_language_mod(lang)
    if mod is None:
        return None
    try:
        from tree_sitter import Language, Parser

        # Some packages expose language_typescript() / language_tsx()
        if lang == "tsx" and hasattr(mod, "language_tsx"):
            ts_lang = Language(mod.language_tsx())
        elif lang == "typescript" and hasattr(mod, "language_typescript"):
            ts_lang = Language(mod.language_typescript())
        else:
            ts_lang = Language(mod.language())
        parser = Parser(ts_lang)
        return parser.parse(source.encode("utf-8"))
    except Exception:
        return None


def _collect_spans(
    root_node: object,  # tree_sitter.Node
    node_types: set[str],
) -> list[tuple[int, int]]:
    """Walk top-level children and collect (start_line, end_line) 0-indexed spans."""
    return [
        (child.start_point[0], child.end_point[0])
        for child in root_node.children  # type: ignore[attr-defined]
        if child.type in node_types
    ]


def _spans_to_chunks(
    spans: list[tuple[int, int]],
    lines: list[str],
    ctx: _ChunkContext,
) -> list[CodeChunk]:
    """Convert AST spans + interstitial gaps into CodeChunks."""
    total_lines = len(lines)
    overlap = max(0, int(ctx.chunk_lines * ctx.overlap_ratio))
    regions: list[tuple[int, int]] = []

    prev_end = 0
    for start, end in sorted(spans):
        if start > prev_end:
            regions.append((prev_end, start - 1))
        regions.append((start, end))
        prev_end = max(prev_end, end + 1)

    if prev_end < total_lines:
        regions.append((prev_end, total_lines - 1))

    chunks: list[CodeChunk] = []
    idx = 0

    for region_start, region_end in regions:
        region_len = region_end - region_start + 1

        if region_len <= ctx.chunk_lines:
            text = "\n".join(lines[region_start : region_end + 1])
            if text.strip():
                chunks.append(CodeChunk(
                    file_path=ctx.path,
                    file_hash=ctx.digest,
                    chunk_idx=idx,
                    start_line=region_start + 1,
                    end_line=region_end + 1,
                    text=text,
                    language=ctx.language,
                ))
                idx += 1
        else:
            step = max(1, ctx.chunk_lines - overlap)
            pos = region_start
            while pos <= region_end:
                sub_end = min(pos + ctx.chunk_lines - 1, region_end)
                text = "\n".join(lines[pos : sub_end + 1])
                if text.strip():
                    chunks.append(CodeChunk(
                        file_path=ctx.path,
                        file_hash=ctx.digest,
                        chunk_idx=idx,
                        start_line=pos + 1,
                        end_line=sub_end + 1,
                        text=text,
                        language=ctx.language,
                    ))
                    idx += 1
                if sub_end >= region_end:
                    break
                pos += step

    return chunks
