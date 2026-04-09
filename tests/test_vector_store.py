"""Tests for the DuckDB vector store (no network required).

Uses small synthetic embeddings so we don't need a real Voyage key.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from somnium.storage.markdown import Chunk
from somnium.storage.vector import VectorStore


def _rand_vec(dim: int = 16, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    return [rng.random() for _ in range(dim)]


def _make_chunk(file_path: Path, idx: int, text: str, file_hash: str) -> Chunk:
    return Chunk(
        file_path=file_path,
        file_hash=file_hash,
        chunk_idx=idx,
        text=text,
    )


def test_init_and_empty_stats(tmp_path: Path) -> None:
    store = VectorStore(tmp_path / "t.duckdb", embedding_dim=16)
    stats = store.stats()
    assert stats["files"] == 0
    assert stats["chunks"] == 0
    assert stats["embedding_dim"] == 16
    store.close()


def test_dim_mismatch_raises(tmp_path: Path) -> None:
    db = tmp_path / "t.duckdb"
    store = VectorStore(db, embedding_dim=16)
    store.close()
    with pytest.raises(ValueError):
        VectorStore(db, embedding_dim=32)


def test_upsert_and_search(tmp_path: Path) -> None:
    db = tmp_path / "t.duckdb"
    store = VectorStore(db, embedding_dim=4)

    file_a = tmp_path / "a.md"
    chunks = [
        _make_chunk(file_a, 0, "Alpha text about cats", "hash-a"),
        _make_chunk(file_a, 1, "Beta text about dogs", "hash-a"),
    ]
    embeddings = [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
    ]
    store.upsert_chunks(
        file_path=str(file_a),
        file_hash="hash-a",
        scope="global",
        chunks=chunks,
        embeddings=embeddings,
    )

    stats = store.stats()
    assert stats["files"] == 1
    assert stats["chunks"] == 2

    # Query: vector identical to first chunk -> first chunk wins
    hits = store.search([1.0, 0.0, 0.0, 0.0], top_k=2, scopes=["global"])
    assert len(hits) == 2
    assert "Alpha" in hits[0].text
    assert hits[0].score > hits[1].score

    store.close()


def test_hash_unchanged_short_circuits(tmp_path: Path) -> None:
    db = tmp_path / "t.duckdb"
    store = VectorStore(db, embedding_dim=4)

    file_a = tmp_path / "a.md"
    chunks = [_make_chunk(file_a, 0, "same content", "hash-1")]
    emb = [[1.0, 0.0, 0.0, 0.0]]
    store.upsert_chunks(
        file_path=str(file_a),
        file_hash="hash-1",
        scope="global",
        chunks=chunks,
        embeddings=emb,
    )
    assert store.get_file_hash(str(file_a)) == "hash-1"

    # Caller logic: if hash matches, skip re-embedding. We verify the
    # recorded hash is readable.
    assert store.get_file_hash(str(file_a)) == "hash-1"
    store.close()


def test_upsert_replaces_existing(tmp_path: Path) -> None:
    db = tmp_path / "t.duckdb"
    store = VectorStore(db, embedding_dim=4)

    file_a = tmp_path / "a.md"
    # Initial: 3 chunks
    chunks = [_make_chunk(file_a, i, f"c{i}", "h1") for i in range(3)]
    store.upsert_chunks(
        file_path=str(file_a),
        file_hash="h1",
        scope="global",
        chunks=chunks,
        embeddings=[[1.0, 0.0, 0.0, 0.0]] * 3,
    )
    assert store.stats()["chunks"] == 3

    # Replace with 1 chunk
    new_chunks = [_make_chunk(file_a, 0, "only one", "h2")]
    store.upsert_chunks(
        file_path=str(file_a),
        file_hash="h2",
        scope="global",
        chunks=new_chunks,
        embeddings=[[0.0, 1.0, 0.0, 0.0]],
    )
    assert store.stats()["chunks"] == 1
    assert store.get_file_hash(str(file_a)) == "h2"
    store.close()


def test_delete_file(tmp_path: Path) -> None:
    db = tmp_path / "t.duckdb"
    store = VectorStore(db, embedding_dim=4)

    file_a = tmp_path / "a.md"
    store.upsert_chunks(
        file_path=str(file_a),
        file_hash="h",
        scope="global",
        chunks=[_make_chunk(file_a, 0, "to delete", "h")],
        embeddings=[[1.0, 0.0, 0.0, 0.0]],
    )
    removed = store.delete_file(str(file_a))
    assert removed == 1
    assert store.stats()["files"] == 0
    store.close()


def test_scope_filter_in_search(tmp_path: Path) -> None:
    db = tmp_path / "t.duckdb"
    store = VectorStore(db, embedding_dim=4)

    file_g = tmp_path / "g.md"
    file_p = tmp_path / "p.md"
    store.upsert_chunks(
        file_path=str(file_g),
        file_hash="hg",
        scope="global",
        chunks=[_make_chunk(file_g, 0, "global memory", "hg")],
        embeddings=[[1.0, 0.0, 0.0, 0.0]],
    )
    store.upsert_chunks(
        file_path=str(file_p),
        file_hash="hp",
        scope="project",
        chunks=[_make_chunk(file_p, 0, "project memory", "hp")],
        embeddings=[[1.0, 0.0, 0.0, 0.0]],
    )

    global_hits = store.search([1.0, 0.0, 0.0, 0.0], top_k=5, scopes=["global"])
    assert len(global_hits) == 1
    assert global_hits[0].scope == "global"

    project_hits = store.search([1.0, 0.0, 0.0, 0.0], top_k=5, scopes=["project"])
    assert len(project_hits) == 1
    assert project_hits[0].scope == "project"

    both_hits = store.search([1.0, 0.0, 0.0, 0.0], top_k=5, scopes=["global", "project"])
    assert len(both_hits) == 2

    store.close()
