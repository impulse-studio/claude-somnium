"""Source code chunking.

Strategy: split files into overlapping line groups. Simple, deterministic,
and works for any language without needing a parser. An AST/tree-sitter
based upgrade is possible later.

Chunk parameters come from config:
  code_search.semantic_chunk_lines  (default 40)

Overlap is 25% of chunk_lines, so consecutive chunks share context.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# File extensions we index as source code. Extend as needed.
DEFAULT_CODE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".kts",
    ".scala",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hh",
    ".hpp",
    ".cs",
    ".rb",
    ".php",
    ".swift",
    ".m",
    ".mm",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".sql",
    ".graphql",
    ".proto",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".html",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".vue",
    ".svelte",
    ".lua",
    ".nim",
    ".ex",
    ".exs",
    ".erl",
    ".hs",
    ".ml",
    ".fs",
    ".fsx",
    ".clj",
    ".cljs",
    ".dart",
    ".r",
    ".jl",
    ".tf",
    ".dockerfile",
}


@dataclass
class CodeChunk:
    file_path: Path
    file_hash: str
    chunk_idx: int
    start_line: int  # 1-indexed inclusive
    end_line: int  # 1-indexed inclusive
    text: str
    language: str = ""

    @property
    def display_text(self) -> str:
        """Text as embedded: breadcrumb + code."""
        header = f"{self.file_path.name}:{self.start_line}-{self.end_line}\n"
        if self.language:
            header = f"[{self.language}] " + header
        return header + self.text


def _detect_language(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    return ext or "text"


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def chunk_source_file(
    path: Path,
    *,
    chunk_lines: int = 40,
    overlap_ratio: float = 0.25,
    max_file_bytes: int = 500_000,
) -> tuple[str, list[CodeChunk]]:
    """Split a source file into overlapping line groups.

    Huge files (> max_file_bytes) are skipped and return empty chunks.
    """
    if not path.exists() or not path.is_file():
        return "", []

    try:
        size = path.stat().st_size
    except OSError:
        return "", []
    if size > max_file_bytes or size == 0:
        return "", []

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return "", []

    lines = raw.splitlines()
    if not lines:
        return "", []

    digest = file_hash(path)
    language = _detect_language(path)

    overlap = max(0, int(chunk_lines * overlap_ratio))
    step = max(1, chunk_lines - overlap)

    chunks: list[CodeChunk] = []
    idx = 0
    start = 0
    total = len(lines)
    while start < total:
        end = min(start + chunk_lines, total)
        text = "\n".join(lines[start:end])
        if text.strip():
            chunks.append(
                CodeChunk(
                    file_path=path,
                    file_hash=digest,
                    chunk_idx=idx,
                    start_line=start + 1,
                    end_line=end,
                    text=text,
                    language=language,
                )
            )
            idx += 1
        if end == total:
            break
        start += step

    return digest, chunks
