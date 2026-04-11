"""High-level indexing orchestration.

Ties markdown parsing + embedding + vector store together. Used both
by the CLI (`somnium index`, `somnium reindex`) and by the hooks
(incremental updates triggered by PostToolUse).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .config import SomniumConfig, get_config
from .embeddings import get_embedder
from .storage.markdown import chunk_file, walk_memory_dir
from .storage.scope import Scope

if TYPE_CHECKING:
    from pathlib import Path

    from .storage.vector import VectorStore


@dataclass
class IndexStats:
    files_seen: int = 0
    files_embedded: int = 0
    files_skipped: int = 0  # hash unchanged
    files_deleted: int = 0
    chunks_upserted: int = 0


def _scope_for_dir(base_dir: Path, kind: str) -> str:  # noqa: ARG001
    """Map a source directory + kind to a scope value."""
    if kind == "memory_global":
        return Scope.GLOBAL.value
    if kind == "memory_project":
        return Scope.PROJECT.value
    if kind == "skill_global":
        return Scope.SKILL_GLOBAL.value
    if kind == "skill_project":
        return Scope.SKILL_PROJECT.value
    return kind


def index_directory(
    *,
    store: VectorStore,
    directory: Path,
    kind: str,
    config: SomniumConfig | None = None,
    prune_missing: bool = True,
) -> IndexStats:
    """Walk `directory`, embed changed files, upsert into `store`."""
    cfg = config or get_config()
    stats = IndexStats()
    scope = _scope_for_dir(directory, kind)

    if not directory.exists():
        return stats

    embedder = get_embedder(cfg)
    files = walk_memory_dir(directory)
    seen_paths: set[str] = set()

    for path in files:
        stats.files_seen += 1
        abs_path = str(path.resolve())
        seen_paths.add(abs_path)

        existing_hash = store.get_file_hash(abs_path)
        new_hash, chunks = chunk_file(path)

        if existing_hash == new_hash and chunks:
            stats.files_skipped += 1
            continue

        if not chunks:
            store.delete_file(abs_path)
            continue

        texts = [c.display_text for c in chunks]
        emb_kind = "code" if kind.startswith("code") else "text"
        result = embedder.embed(texts, kind=emb_kind, input_type="document")

        store.upsert_chunks(
            file_path=abs_path,
            file_hash=new_hash,
            scope=scope,
            chunks=chunks,
            embeddings=result.embeddings,
        )
        stats.files_embedded += 1
        stats.chunks_upserted += len(chunks)

    if prune_missing:
        stored_paths = _list_stored_paths(store, scope)
        for stored in stored_paths:
            if stored not in seen_paths:
                removed = store.delete_file(stored)
                if removed > 0:
                    stats.files_deleted += 1

    return stats


def _list_stored_paths(store: VectorStore, scope: str) -> list[str]:
    """Return all file_paths currently stored for a given scope."""
    with store._lock:  # noqa: SLF001 — internal access for a small helper
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT file_path FROM files WHERE scope = ?", [scope]
        ).fetchall()
    return [row[0] for row in rows]


def index_single_file(
    *,
    store: VectorStore,
    path: Path,
    kind: str,
    config: SomniumConfig | None = None,
) -> IndexStats:
    """Index a single markdown file (used by PostToolUse hook)."""
    cfg = config or get_config()
    stats = IndexStats()
    if not path.exists():
        removed = store.delete_file(str(path.resolve()))
        if removed > 0:
            stats.files_deleted += 1
        return stats

    stats.files_seen = 1
    scope = _scope_for_dir(path.parent, kind)
    abs_path = str(path.resolve())

    existing_hash = store.get_file_hash(abs_path)
    new_hash, chunks = chunk_file(path)

    if existing_hash == new_hash and chunks:
        stats.files_skipped = 1
        return stats

    if not chunks:
        store.delete_file(abs_path)
        return stats

    embedder = get_embedder(cfg)
    texts = [c.display_text for c in chunks]
    emb_kind = "code" if kind.startswith("code") else "text"
    result = embedder.embed(texts, kind=emb_kind, input_type="document")

    store.upsert_chunks(
        file_path=abs_path,
        file_hash=new_hash,
        scope=scope,
        chunks=chunks,
        embeddings=result.embeddings,
    )
    stats.files_embedded = 1
    stats.chunks_upserted = len(chunks)
    return stats
