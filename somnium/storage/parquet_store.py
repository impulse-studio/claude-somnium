"""Parquet-backed vector store with DuckDB cache.

Source of truth: a ``.parquet`` file committed to git.
Fast local cache: a ``.duckdb`` file in ``~/.claude/somnium/cache/``.

On open, the Parquet hash is compared to the cached hash. If they
differ, the DuckDB is rebuilt from the Parquet (~1s). Writes go to
both DuckDB and Parquet atomically.

This gives us:
  - Fast search (~10ms via DuckDB on disk)
  - Shareable index (Parquet in git, merge driver for conflicts)
  - No manual rebuild after ``git pull``
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .vector import VectorStore

_CACHE_ROOT = Path.home() / ".claude" / "somnium" / "cache"


def _cache_dir_for(parquet_path: Path) -> Path:
    """Deterministic cache directory for a Parquet file path."""
    key = str(parquet_path.resolve())
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return _CACHE_ROOT / digest


def _hash_file(path: Path) -> str:
    """Fast SHA-256 of a file's contents. Returns '' if missing."""
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class ParquetStore:
    """Vector store backed by Parquet (source of truth) + DuckDB (local cache).

    Use as a context manager. On enter, syncs the DuckDB from Parquet
    if needed. On exit, exports DuckDB → Parquet if writes occurred.
    """

    def __init__(
        self,
        parquet_path: Path,
        embedding_dim: int = 1024,
    ) -> None:
        self._parquet_path = parquet_path
        self._embedding_dim = embedding_dim
        self._cache_dir = _cache_dir_for(parquet_path)
        self._duckdb_path = self._cache_dir / parquet_path.name.replace(".parquet", ".duckdb")
        self._hash_path = self._cache_dir / parquet_path.name.replace(".parquet", ".hash")
        self._store: VectorStore | None = None
        self._dirty = False

    def __enter__(self) -> VectorStore:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._sync_from_parquet()
        self._store = VectorStore(self._duckdb_path, embedding_dim=self._embedding_dim)
        # Monkey-patch close to track writes
        original_upsert = self._store.upsert_chunks
        original_delete = self._store.delete_file

        def tracked_upsert(*a: object, **kw: object) -> None:
            self._dirty = True
            return original_upsert(*a, **kw)  # type: ignore[arg-type]

        def tracked_delete(*a: object, **kw: object) -> int:
            self._dirty = True
            return original_delete(*a, **kw)  # type: ignore[arg-type]

        self._store.upsert_chunks = tracked_upsert  # type: ignore[assignment]
        self._store.delete_file = tracked_delete  # type: ignore[assignment]
        return self._store

    def __exit__(self, *args: object) -> None:
        if self._dirty and self._store is not None:
            self._export_to_parquet()
        if self._store is not None:
            self._store.close()
            self._store = None

    def _sync_from_parquet(self) -> None:
        """Rebuild DuckDB from Parquet if the cache is stale."""
        parquet_hash = _hash_file(self._parquet_path)
        if not parquet_hash:
            # No Parquet yet — fresh index, DuckDB will be created empty
            return

        cached_hash = self._hash_path.read_text(encoding="utf-8").strip() if self._hash_path.exists() else ""
        if cached_hash == parquet_hash and self._duckdb_path.exists():
            return  # cache is fresh

        # Rebuild: create a new VectorStore, import from Parquet
        if self._duckdb_path.exists():
            self._duckdb_path.unlink()
        wal = self._duckdb_path.with_suffix(".duckdb.wal")
        if wal.exists():
            wal.unlink()

        store = VectorStore(self._duckdb_path, embedding_dim=self._embedding_dim)
        import duckdb as _ddb

        conn = _ddb.connect(":memory:")
        conn.execute("CREATE TABLE pq AS SELECT * FROM read_parquet(?)", [str(self._parquet_path)])

        # Insert into the VectorStore's DuckDB from the Parquet data
        rows = conn.execute(
            "SELECT file_path, file_hash, scope FROM pq GROUP BY file_path, file_hash, scope"
        ).fetchall()
        for file_path, file_hash, scope in rows:
            store._conn.execute(  # noqa: SLF001
                """INSERT INTO files (file_path, file_hash, scope)
                   VALUES (?, ?, ?) ON CONFLICT DO NOTHING""",
                [file_path, file_hash, scope],
            )

        chunk_rows = conn.execute(
            "SELECT file_path, chunk_idx, text, heading_path, scope, tags, embedding FROM pq"
        ).fetchall()
        for file_path, chunk_idx, text, heading_path, scope, tags, embedding in chunk_rows:
            store._conn.execute(  # noqa: SLF001
                """INSERT INTO chunks (file_path, chunk_idx, text, heading_path, scope, tags, embedding)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [file_path, chunk_idx, text, heading_path, scope, tags, list(embedding)],
            )

        conn.close()
        store.close()

        self._hash_path.write_text(parquet_hash, encoding="utf-8")

    def _export_to_parquet(self) -> None:
        """Export the DuckDB to Parquet and update the hash."""
        if self._store is None:
            return
        self._parquet_path.parent.mkdir(parents=True, exist_ok=True)
        with self._store._lock:  # noqa: SLF001
            self._store._conn.execute(  # noqa: SLF001
                """COPY (
                    SELECT c.file_path, f.file_hash, c.chunk_idx, c.text,
                           c.heading_path, c.scope, c.tags, c.embedding
                    FROM chunks c
                    JOIN files f ON c.file_path = f.file_path
                    ORDER BY c.file_path, c.chunk_idx
                ) TO ? (FORMAT PARQUET, COMPRESSION ZSTD)""",
                [str(self._parquet_path)],
            )
        # Update cached hash
        new_hash = _hash_file(self._parquet_path)
        self._hash_path.write_text(new_hash, encoding="utf-8")
