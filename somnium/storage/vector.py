"""DuckDB-backed vector store.

One DuckDB file per index (e.g. global index, per-project code index).
Markdown files are the source of truth: if this DB is deleted, we can
rebuild it by re-walking the memory directory and re-embedding.

Schema (per store):
  files(file_path PK, file_hash, scope, indexed_at)
  chunks(id PK, file_path FK, chunk_idx, text, heading_path, scope,
         tags, embedding FLOAT[DIM], created_at)
  meta(key PK, value)  -- stores embedding_dim, provider, model

Upsert strategy:
  1. Compute file_hash. If equal to the stored hash, no-op.
  2. Otherwise, delete all existing chunks for this file, insert new
     chunks and their embeddings, then update files.file_hash.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

if TYPE_CHECKING:
    from .markdown import Chunk


@dataclass
class SearchHit:
    """A single vector search result."""

    file_path: str
    chunk_idx: int
    text: str
    heading_path: list[str]
    scope: str
    tags: list[str]
    score: float

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "chunk_idx": self.chunk_idx,
            "text": self.text,
            "heading_path": self.heading_path,
            "scope": self.scope,
            "tags": self.tags,
            "score": self.score,
        }


class VectorStore:
    """DuckDB vector store. Not thread-safe across instances — use one
    VectorStore per logical index per process.

    Use as a context manager or call .close() when done.
    """

    def __init__(self, db_path: Path, embedding_dim: int = 1024):
        self.db_path = db_path
        self.embedding_dim = embedding_dim
        self._lock = threading.Lock()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(db_path))
        self._init_schema()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        dim = self.embedding_dim
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key VARCHAR PRIMARY KEY,
                    value VARCHAR
                )
                """
            )
            # Enforce dimension consistency across runs.
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = 'embedding_dim'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('embedding_dim', ?)",
                    [str(dim)],
                )
            else:
                stored = int(row[0])
                if stored != dim:
                    raise ValueError(
                        f"Embedding dimension mismatch at {self.db_path}: "
                        f"store has {stored}, config wants {dim}. "
                        f"Delete the DB file to rebuild with the new dim."
                    )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    file_path VARCHAR PRIMARY KEY,
                    file_hash VARCHAR NOT NULL,
                    scope VARCHAR NOT NULL,
                    indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            self._conn.execute("CREATE SEQUENCE IF NOT EXISTS chunks_id_seq")
            self._conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    id BIGINT PRIMARY KEY DEFAULT nextval('chunks_id_seq'),
                    file_path VARCHAR NOT NULL,
                    chunk_idx INTEGER NOT NULL,
                    text VARCHAR NOT NULL,
                    heading_path VARCHAR,
                    scope VARCHAR NOT NULL,
                    tags VARCHAR,
                    embedding FLOAT[{dim}],
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(file_path, chunk_idx)
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_file_path ON chunks(file_path)"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_scope ON chunks(scope)"
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> VectorStore:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO meta (key, value) VALUES (?, ?)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value
                """,
                [key, value],
            )

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key = ?", [key]
            ).fetchone()
            return row[0] if row else None

    # ------------------------------------------------------------------
    # Upsert / delete
    # ------------------------------------------------------------------

    def get_file_hash(self, file_path: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT file_hash FROM files WHERE file_path = ?", [file_path]
            ).fetchone()
            return row[0] if row else None

    def delete_file(self, file_path: str) -> int:
        """Remove a file and all its chunks. Returns number of chunks removed."""
        with self._lock:
            count = self._conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE file_path = ?", [file_path]
            ).fetchone()[0]
            self._conn.execute("DELETE FROM chunks WHERE file_path = ?", [file_path])
            self._conn.execute("DELETE FROM files WHERE file_path = ?", [file_path])
            return int(count)

    def upsert_chunks(
        self,
        file_path: str,
        file_hash: str,
        scope: str,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        tags: list[str] | None = None,
    ) -> None:
        """Replace all chunks for a file and record its hash."""
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks ({len(chunks)}) and embeddings ({len(embeddings)}) length mismatch"
            )
        for emb in embeddings:
            if len(emb) != self.embedding_dim:
                raise ValueError(
                    f"Embedding has dim {len(emb)}, expected {self.embedding_dim}"
                )

        tags_json = json.dumps(tags or [])
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute(
                    "DELETE FROM chunks WHERE file_path = ?", [file_path]
                )
                self._conn.execute(
                    """
                    INSERT INTO files (file_path, file_hash, scope, indexed_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT (file_path) DO UPDATE SET
                        file_hash = excluded.file_hash,
                        scope = excluded.scope,
                        indexed_at = excluded.indexed_at
                    """,
                    [file_path, file_hash, scope],
                )
                for chunk, emb in zip(chunks, embeddings, strict=True):
                    heading_json = json.dumps(chunk.heading_path)
                    self._conn.execute(
                        """
                        INSERT INTO chunks (
                            file_path, chunk_idx, text, heading_path,
                            scope, tags, embedding
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            file_path,
                            chunk.chunk_idx,
                            chunk.display_text,
                            heading_json,
                            scope,
                            tags_json,
                            emb,
                        ],
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 5,
        scopes: list[str] | None = None,
    ) -> list[SearchHit]:
        """Brute-force cosine similarity search.

        Fine up to ~100k chunks. If/when scale matters we can add an
        HNSW index via the DuckDB `vss` extension.
        """
        if len(query_embedding) != self.embedding_dim:
            raise ValueError(
                f"Query embedding dim {len(query_embedding)} != {self.embedding_dim}"
            )

        where_sql = ""
        params: list = [query_embedding]
        if scopes:
            placeholders = ",".join(["?"] * len(scopes))
            where_sql = f"WHERE scope IN ({placeholders})"
            params.extend(scopes)
        params.append(top_k)

        sql = f"""
            SELECT
                file_path,
                chunk_idx,
                text,
                heading_path,
                scope,
                tags,
                array_cosine_similarity(embedding, ?::FLOAT[{self.embedding_dim}]) AS score
            FROM chunks
            {where_sql}
            ORDER BY score DESC
            LIMIT ?
        """

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        hits: list[SearchHit] = []
        for file_path, chunk_idx, text, heading_json, scope, tags_json, score in rows:
            hits.append(
                SearchHit(
                    file_path=file_path,
                    chunk_idx=int(chunk_idx),
                    text=text,
                    heading_path=json.loads(heading_json) if heading_json else [],
                    scope=scope,
                    tags=json.loads(tags_json) if tags_json else [],
                    score=float(score),
                )
            )
        return hits

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        with self._lock:
            n_files = self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            n_chunks = self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            per_scope_rows = self._conn.execute(
                "SELECT scope, COUNT(*) FROM chunks GROUP BY scope"
            ).fetchall()
        return {
            "files": int(n_files),
            "chunks": int(n_chunks),
            "per_scope": {row[0]: int(row[1]) for row in per_scope_rows},
            "embedding_dim": self.embedding_dim,
        }
