"""Tests for the semantic code search query interface."""

from __future__ import annotations

import pytest

from somnium import indexer as memory_indexer
from somnium.code import indexer as code_indexer
from somnium.code import semantic as semantic_module
from somnium.code.indexer import CODE_SCOPE, index_repo_code
from somnium.code.semantic import _parse_hit, search_code
from somnium.config import SomniumConfig
from somnium.embeddings.voyage import EmbedResult
from somnium.storage.parquet_store import ParquetStore
from somnium.storage.vector import SearchHit, VectorStore


class _FakeEmbedder:
    def embed(self, texts, *, kind="text", input_type="document"):
        return EmbedResult(
            embeddings=[[1.0, 0.0, 0.0, 0.0] for _ in texts],
            model="fake",
            input_type=input_type,
        )

    def embed_query(self, text, *, kind="text"):
        return [1.0, 0.0, 0.0, 0.0]

    def model_for(self, kind):
        return "fake"


# ---------------------------------------------------------------------------
# _parse_hit — pure function, no fixtures needed
# ---------------------------------------------------------------------------


def test_parse_hit_extracts_language_and_line_range():
    raw = SearchHit(
        file_path="/repo/foo.py",
        chunk_idx=0,
        text="[py] foo.py:1-40\n\ndef foo(): pass",
        heading_path=["[py] foo.py:1-40"],
        scope=CODE_SCOPE,
        tags=[],
        score=0.9,
    )
    hit = _parse_hit(raw)
    assert hit.language == "py"
    assert hit.start_line == 1
    assert hit.end_line == 40
    assert hit.text == "def foo(): pass"
    assert hit.score == 0.9


def test_parse_hit_handles_missing_breadcrumb():
    raw = SearchHit(
        file_path="/repo/x.py",
        chunk_idx=0,
        text="def x(): pass",  # no breadcrumb prefix
        heading_path=[],
        scope=CODE_SCOPE,
        tags=[],
        score=0.5,
    )
    hit = _parse_hit(raw)
    assert hit.language == ""
    assert hit.start_line is None
    assert hit.end_line is None
    assert "def x()" in hit.text


def test_parse_hit_handles_malformed_breadcrumb():
    """If the breadcrumb is mangled, we should still return a hit
    rather than crash — it just lacks line range / language."""
    raw = SearchHit(
        file_path="/repo/y.go",
        chunk_idx=0,
        text="garbage:not-a-range\n\nbody",
        heading_path=["garbage:not-a-range"],
        scope=CODE_SCOPE,
        tags=[],
        score=0.4,
    )
    hit = _parse_hit(raw)
    assert hit.start_line is None
    assert hit.end_line is None
    assert "body" in hit.text


# ---------------------------------------------------------------------------
# search_code — needs a sandbox config + seeded index
# ---------------------------------------------------------------------------


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "auth.py").write_text(
        "def authenticate(token):\n    return validate(token)\n", encoding="utf-8"
    )
    (repo / "src" / "user.py").write_text(
        "class User:\n    def __init__(self, name):\n        self.name = name\n",
        encoding="utf-8",
    )

    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "home")
    cfg.project_root = repo

    fake = _FakeEmbedder()
    monkeypatch.setattr(memory_indexer, "get_embedder", lambda c=None: fake)
    monkeypatch.setattr(code_indexer, "get_embedder", lambda c=None: fake)
    monkeypatch.setattr(semantic_module, "get_embedder", lambda c=None: fake)

    # Force dim=4 for the code index (ParquetStore in semantic, VectorStore in code_indexer)
    real_ps = semantic_module.ParquetStore

    def _ps(path, embedding_dim=4):
        return real_ps(path, embedding_dim=4)

    monkeypatch.setattr(semantic_module, "ParquetStore", _ps)
    monkeypatch.setattr(code_indexer, "VectorStore", lambda path, embedding_dim=4: VectorStore(path, embedding_dim=4))

    return cfg, repo


def test_search_code_returns_empty_when_no_project(monkeypatch):
    cfg = SomniumConfig()
    cfg.project_root = None
    hits = search_code("anything", config=cfg)
    assert hits == []


def test_search_code_returns_empty_when_index_missing(sandbox):
    cfg, _ = sandbox
    # No index has been built yet
    assert not cfg.project_code_index_path.exists()
    hits = search_code("authenticate", config=cfg)
    assert hits == []


def test_search_code_returns_indexed_chunks(sandbox):
    cfg, repo = sandbox
    # Bootstrap the code index via ParquetStore so the .parquet exists for search_code
    with ParquetStore(cfg.project_code_index_path, embedding_dim=4) as store:
        stats = index_repo_code(root=repo, store=store, config=cfg)
    assert stats.files_embedded == 2

    hits = search_code("authentication function", top_k=5, config=cfg)
    assert len(hits) >= 1
    paths = {h.file_path for h in hits}
    assert any("auth.py" in p or "user.py" in p for p in paths)
    # Each hit should have language metadata extracted
    for h in hits:
        assert h.language in ("py", "")


def test_search_code_respects_top_k(sandbox):
    cfg, repo = sandbox
    with ParquetStore(cfg.project_code_index_path, embedding_dim=4) as store:
        index_repo_code(root=repo, store=store, config=cfg)

    hits = search_code("anything", top_k=1, config=cfg)
    assert len(hits) <= 1
