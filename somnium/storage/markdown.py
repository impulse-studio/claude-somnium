"""Markdown parsing and chunking for memory files.

Design:
  - Each .md file = one or more chunks.
  - Frontmatter is parsed and flows into chunk metadata.
  - Chunking strategy: split by H1/H2/H3 sections. Big sections are
    further split by paragraphs into sub-chunks, each bounded by
    MAX_CHUNK_CHARS. Small adjacent sections are coalesced.
  - Each chunk keeps the parent heading chain as context so it reads
    standalone in search results.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import frontmatter

MAX_CHUNK_CHARS = 1500
# Chunks shorter than this are only dropped when they come from splitting
# an oversized section into sub-pieces; legitimate short top-level
# sections are always kept.
MIN_SUBCHUNK_CHARS = 120

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass
class Chunk:
    """A single indexable chunk of a markdown file."""

    file_path: Path
    file_hash: str
    chunk_idx: int
    text: str
    heading_path: list[str] = field(default_factory=list)
    frontmatter_data: dict[str, Any] = field(default_factory=dict)

    @property
    def display_text(self) -> str:
        """Text as embedded: heading breadcrumb + body."""
        if not self.heading_path:
            return self.text
        breadcrumb = " > ".join(self.heading_path)
        return f"{breadcrumb}\n\n{self.text}"


def file_hash(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _split_into_sections(body: str) -> list[tuple[list[str], str]]:
    """Split a markdown body into (heading_path, section_body) tuples.

    The heading path is the chain of ancestor headings, e.g. ["H1", "H2"].
    """
    # Walk the lines and keep a stack of headings by level.
    sections: list[tuple[list[str], str]] = []
    current_stack: list[tuple[int, str]] = []  # (level, text)
    current_body: list[str] = []

    def flush() -> None:
        if not current_body and not current_stack:
            return
        text = "\n".join(current_body).strip()
        if not text:
            return
        path = [title for _, title in current_stack]
        sections.append((path, text))

    for line in body.splitlines():
        m = HEADING_RE.match(line)
        if m:
            flush()
            current_body = []
            level = len(m.group(1))
            title = m.group(2)
            # Pop any deeper-or-equal headings off the stack
            while current_stack and current_stack[-1][0] >= level:
                current_stack.pop()
            current_stack.append((level, title))
            continue
        current_body.append(line)

    flush()
    # If the file has no headings at all, return the whole body as one
    # section with an empty heading path.
    if not sections:
        text = body.strip()
        if text:
            sections.append(([], text))
    return sections


def _split_oversized(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split an oversized section by paragraphs, respecting max_chars."""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for para in re.split(r"\n{2,}", text):
        para = para.strip()  # noqa: PLW2901
        if not para:
            continue
        if buf_len + len(para) + 2 > max_chars and buf:
            parts.append("\n\n".join(buf))
            buf = [para]
            buf_len = len(para)
        else:
            buf.append(para)
            buf_len += len(para) + 2
    if buf:
        parts.append("\n\n".join(buf))
    return parts


def chunk_file(path: Path) -> tuple[str, list[Chunk]]:
    """Parse a markdown file and return (file_hash, chunks).

    Returns the hash even if the file yields zero chunks so callers can
    record the hash and skip re-embedding next time.
    """
    digest = file_hash(path)
    raw = path.read_text(encoding="utf-8")
    post = frontmatter.loads(raw)
    fm_data = dict(post.metadata or {})
    body = post.content or ""

    sections = _split_into_sections(body)
    chunks: list[Chunk] = []
    idx = 0
    for heading_path, section_text in sections:
        pieces = _split_oversized(section_text)
        was_split = len(pieces) > 1
        for piece in pieces:
            stripped = piece.strip()
            if not stripped:
                continue
            # Only drop tiny fragments that came from splitting an
            # oversized section — a legitimate short section is kept.
            if was_split and len(stripped) < MIN_SUBCHUNK_CHARS:
                continue
            chunks.append(
                Chunk(
                    file_path=path,
                    file_hash=digest,
                    chunk_idx=idx,
                    text=piece,
                    heading_path=list(heading_path),
                    frontmatter_data=fm_data,
                )
            )
            idx += 1

    # Edge case: a small file that was skipped entirely. Emit one chunk
    # with whatever body we have so nothing gets silently lost.
    if not chunks and body.strip():
        chunks.append(
            Chunk(
                file_path=path,
                file_hash=digest,
                chunk_idx=0,
                text=body.strip(),
                heading_path=[],
                frontmatter_data=fm_data,
            )
        )

    return digest, chunks


def walk_memory_dir(root: Path) -> list[Path]:
    """Return all .md files under `root`, sorted for determinism."""
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.md") if p.is_file())
