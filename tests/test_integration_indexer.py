"""End-to-end test of the indexer pipeline with a fake embedder.

Verifies that markdown files on disk → chunking → embedding → DuckDB →
search round-trips correctly, without hitting the Voyage API.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from somnium import indexer
from somnium.embeddings.voyage import EmbedResult
from somnium.indexer import index_directory, index_single_file
from somnium.storage.vector import VectorStore


@dataclass
class FakeEmbedder:
    """Deterministic toy embedder: maps keywords to simple vectors.

    Dim = 4. Each embedding is a one-hot-ish vector based on keyword
    presence so similarity math is predictable.
    """

    dim: int = 4

    def embed(self, texts, *, kind="text", input_type="document"):
        vecs = []
        for text in texts:
            v = [0.0, 0.0, 0.0, 0.0]
            lower = text.lower()
            if "graphite" in lower or "push" in lower or "git" in lower:
                v[0] = 1.0
            if "react" in lower or "component" in lower or "frontend" in lower:
                v[1] = 1.0
            if "database" in lower or "sql" in lower:
                v[2] = 1.0
            # Ensure non-zero so cosine is defined
            if sum(v) == 0:
                v[3] = 1.0
            vecs.append(v)
        return EmbedResult(embeddings=vecs, model="fake", input_type=input_type)

    def embed_query(self, text, *, kind="text"):
        return self.embed([text], kind=kind, input_type="query").embeddings[0]

    def model_for(self, kind):
        return "fake"


@pytest.fixture
def fake_embedder(monkeypatch):
    fake = FakeEmbedder()
    # Patch get_embedder everywhere it's imported
    monkeypatch.setattr(indexer, "get_embedder", lambda cfg=None: fake)
    return fake


def _write_memory(dir: Path, name: str, body: str) -> Path:
    path = dir / name
    path.write_text(body, encoding="utf-8")
    return path


def test_full_pipeline_index_and_search(tmp_path: Path, fake_embedder) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()

    _write_memory(
        memory_dir,
        "graphite.md",
        "# Graphite workflow\n\nAlways use graphite gt submit to push.\n",
    )
    _write_memory(
        memory_dir,
        "react.md",
        "# React components\n\nShared React components live in src/components/.\n",
    )
    _write_memory(
        memory_dir,
        "db.md",
        "# Database access\n\nUse sql queries via the repository layer.\n",
    )

    store = VectorStore(tmp_path / "idx.duckdb", embedding_dim=4)
    stats = index_directory(
        store=store,
        directory=memory_dir,
        kind="memory_global",
    )
    assert stats.files_seen == 3
    assert stats.files_embedded == 3
    assert stats.chunks_upserted >= 3

    # Query for git/graphite content — should find graphite.md
    hits = store.search(
        query_embedding=fake_embedder.embed_query("how to push with git"),
        top_k=3,
        scopes=["global"],
    )
    assert len(hits) >= 1
    assert any("graphite" in h.file_path.lower() for h in hits)
    assert hits[0].scope == "global"

    # Query for react
    hits = store.search(
        query_embedding=fake_embedder.embed_query("where do react components live"),
        top_k=3,
        scopes=["global"],
    )
    assert any("react" in h.file_path.lower() for h in hits)

    store.close()


def test_hash_skip_on_second_run(tmp_path: Path, fake_embedder) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "note.md", "# Note\n\nSome stable body text.\n")

    store = VectorStore(tmp_path / "idx.duckdb", embedding_dim=4)
    first = index_directory(store=store, directory=memory_dir, kind="memory_global")
    assert first.files_embedded == 1

    # Second pass, same content -> nothing re-embedded
    second = index_directory(store=store, directory=memory_dir, kind="memory_global")
    assert second.files_embedded == 0
    assert second.files_skipped == 1
    store.close()


def test_reindex_after_edit(tmp_path: Path, fake_embedder) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    p = _write_memory(memory_dir, "a.md", "# Old\n\nOld body about cats.\n")

    store = VectorStore(tmp_path / "idx.duckdb", embedding_dim=4)
    index_directory(store=store, directory=memory_dir, kind="memory_global")
    assert store.stats()["files"] == 1

    # Edit the file, re-run indexer
    p.write_text("# New\n\nNew body about react and components.\n", encoding="utf-8")
    stats = index_directory(store=store, directory=memory_dir, kind="memory_global")
    assert stats.files_embedded == 1
    assert stats.files_skipped == 0
    assert store.stats()["files"] == 1  # same file, replaced
    store.close()


def test_deleted_file_is_pruned(tmp_path: Path, fake_embedder) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    _write_memory(memory_dir, "keep.md", "# Keep\n\nStill here body.\n")
    gone = _write_memory(memory_dir, "gone.md", "# Gone\n\nWill be deleted.\n")

    store = VectorStore(tmp_path / "idx.duckdb", embedding_dim=4)
    index_directory(store=store, directory=memory_dir, kind="memory_global")
    assert store.stats()["files"] == 2

    gone.unlink()
    stats = index_directory(store=store, directory=memory_dir, kind="memory_global")
    assert stats.files_deleted == 1
    assert store.stats()["files"] == 1
    store.close()


def test_single_file_indexer_handles_delete(tmp_path: Path, fake_embedder) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    p = _write_memory(memory_dir, "once.md", "# Once\n\nBody.\n")

    store = VectorStore(tmp_path / "idx.duckdb", embedding_dim=4)
    index_single_file(store=store, path=p, kind="memory_global")
    assert store.stats()["files"] == 1

    p.unlink()
    stats = index_single_file(store=store, path=p, kind="memory_global")
    assert stats.files_deleted == 1
    assert store.stats()["files"] == 0
    store.close()
