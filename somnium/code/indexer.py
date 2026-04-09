"""Code indexer: walks a repo and upserts chunks into the code vector store.

This is the on-demand `somnium index --code` entry point, plus the
incremental path used by the PostToolUse hook for single-file updates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import SomniumConfig, get_config
from ..embeddings import get_embedder
from ..storage.markdown import Chunk as MemoryChunk
from ..storage.vector import VectorStore
from .chunker import CodeChunk, chunk_source_file
from .walker import walk_code

CODE_SCOPE = "code"


@dataclass
class CodeIndexStats:
    files_seen: int = 0
    files_embedded: int = 0
    files_skipped: int = 0
    files_deleted: int = 0
    chunks_upserted: int = 0
    skipped_large: int = 0


def _to_memory_chunk(cc: CodeChunk) -> MemoryChunk:
    """Adapt a CodeChunk to the MemoryChunk interface expected by VectorStore.

    We carry the line range in the heading_path so the query layer can
    recover start/end lines, and keep the body text clean (no prefix),
    because MemoryChunk.display_text will render breadcrumb + text.
    """
    breadcrumb = f"[{cc.language}] {cc.file_path.name}:{cc.start_line}-{cc.end_line}"
    return MemoryChunk(
        file_path=cc.file_path,
        file_hash=cc.file_hash,
        chunk_idx=cc.chunk_idx,
        text=cc.text,
        heading_path=[breadcrumb],
    )


def index_repo_code(
    *,
    root: Path,
    store: VectorStore,
    config: SomniumConfig | None = None,
    prune_missing: bool = True,
) -> CodeIndexStats:
    """Walk `root` and upsert all source files into `store`."""
    cfg = config or get_config()
    stats = CodeIndexStats()

    chunk_lines = cfg.code_search.semantic_chunk_lines
    ignore = cfg.code_search.ignore

    files = walk_code(root, ignore=ignore)
    if not files:
        return stats

    embedder = get_embedder(cfg)
    seen_paths: set[str] = set()

    for path in files:
        stats.files_seen += 1
        abs_path = str(path.resolve())
        seen_paths.add(abs_path)

        existing_hash = store.get_file_hash(abs_path)
        digest, cchunks = chunk_source_file(path, chunk_lines=chunk_lines)

        if not digest and not cchunks:
            stats.skipped_large += 1
            continue
        if existing_hash == digest and cchunks:
            stats.files_skipped += 1
            continue
        if not cchunks:
            store.delete_file(abs_path)
            continue

        # Translate into the shape VectorStore expects.
        mem_chunks = [_to_memory_chunk(cc) for cc in cchunks]
        texts = [cc.display_text for cc in cchunks]
        result = embedder.embed(texts, kind="code", input_type="document")

        store.upsert_chunks(
            file_path=abs_path,
            file_hash=digest,
            scope=CODE_SCOPE,
            chunks=mem_chunks,
            embeddings=result.embeddings,
        )
        stats.files_embedded += 1
        stats.chunks_upserted += len(cchunks)

    if prune_missing:
        stored_paths = _list_stored_paths(store, CODE_SCOPE)
        for stored in stored_paths:
            if stored not in seen_paths:
                removed = store.delete_file(stored)
                if removed > 0:
                    stats.files_deleted += 1

    return stats


def _list_stored_paths(store: VectorStore, scope: str) -> list[str]:
    with store._lock:
        rows = store._conn.execute(
            "SELECT file_path FROM files WHERE scope = ?", [scope]
        ).fetchall()
    return [row[0] for row in rows]


def index_single_code_file(
    *,
    store: VectorStore,
    path: Path,
    config: SomniumConfig | None = None,
) -> CodeIndexStats:
    """Reindex a single code file (used by PostToolUse hook)."""
    cfg = config or get_config()
    stats = CodeIndexStats()

    if not path.exists():
        removed = store.delete_file(str(path.resolve()))
        if removed > 0:
            stats.files_deleted += 1
        return stats

    stats.files_seen = 1
    abs_path = str(path.resolve())
    existing_hash = store.get_file_hash(abs_path)
    digest, cchunks = chunk_source_file(path, chunk_lines=cfg.code_search.semantic_chunk_lines)

    if not digest and not cchunks:
        stats.skipped_large = 1
        return stats
    if existing_hash == digest and cchunks:
        stats.files_skipped = 1
        return stats
    if not cchunks:
        store.delete_file(abs_path)
        return stats

    embedder = get_embedder(cfg)
    mem_chunks = [_to_memory_chunk(cc) for cc in cchunks]
    texts = [cc.display_text for cc in cchunks]
    result = embedder.embed(texts, kind="code", input_type="document")

    store.upsert_chunks(
        file_path=abs_path,
        file_hash=digest,
        scope=CODE_SCOPE,
        chunks=mem_chunks,
        embeddings=result.embeddings,
    )
    stats.files_embedded = 1
    stats.chunks_upserted = len(cchunks)
    return stats
