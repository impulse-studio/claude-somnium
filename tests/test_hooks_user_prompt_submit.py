"""Tests for the UserPromptSubmit context injection hook."""

from __future__ import annotations

from pathlib import Path

import pytest

from somnium import indexer
from somnium.config import SomniumConfig
from somnium.embeddings.voyage import EmbedResult
from somnium.hooks import user_prompt_submit as hook
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


@pytest.fixture
def seeded_cfg(tmp_path: Path, monkeypatch):
    global_root = tmp_path / "home"
    (global_root / "memory").mkdir(parents=True)

    cfg = SomniumConfig()
    cfg.storage.global_root = str(global_root)
    cfg.embeddings.api_key = "fake"  # bypass the "no key" skip
    cfg.context_injection.top_k = 3
    cfg.context_injection.context_budget_tokens = 500

    # Patch load_config and get_embedder used by the hook module.
    monkeypatch.setattr(hook, "load_config", lambda project_root=None: cfg)

    # Force hook-constructed VectorStore instances to use dim=4.
    _real_vs = hook.VectorStore

    def _vs_with_dim(db_path, embedding_dim=4):
        return _real_vs(db_path, embedding_dim=4)

    monkeypatch.setattr(hook, "VectorStore", _vs_with_dim)

    fake = _FakeEmbedder()

    def _fake_get_embedder(config=None):
        return fake

    monkeypatch.setattr(
        "somnium.embeddings.get_embedder", _fake_get_embedder
    )
    monkeypatch.setattr(indexer, "get_embedder", _fake_get_embedder)

    # Seed the global index with a couple of chunks.
    store = VectorStore(cfg.global_index_path, embedding_dim=4)
    from somnium.storage.markdown import Chunk

    chunk1 = Chunk(
        file_path=global_root / "memory" / "a.md",
        file_hash="h",
        chunk_idx=0,
        text="Always use graphite gt submit instead of git push.",
    )
    chunk2 = Chunk(
        file_path=global_root / "memory" / "b.md",
        file_hash="h",
        chunk_idx=0,
        text="Always use uv for Python packaging.",
    )
    store.upsert_chunks(
        file_path=str(chunk1.file_path),
        file_hash="h",
        scope="global",
        chunks=[chunk1],
        embeddings=[[1.0, 0.0, 0.0, 0.0]],
    )
    store.upsert_chunks(
        file_path=str(chunk2.file_path),
        file_hash="h",
        scope="global",
        chunks=[chunk2],
        embeddings=[[0.9, 0.1, 0.0, 0.0]],
    )
    store.close()

    return cfg


def test_empty_prompt_is_skipped(seeded_cfg):
    result = hook.handle_event({"prompt": ""})
    assert "skipped" in result


def test_disabled_injection_is_skipped(seeded_cfg, monkeypatch):
    seeded_cfg.context_injection.enabled = False
    result = hook.handle_event({"prompt": "how do I push"})
    assert result["skipped"] == "context_injection disabled"


def test_missing_api_key_is_skipped(seeded_cfg, monkeypatch):
    seeded_cfg.embeddings.api_key = None
    seeded_cfg.embeddings.api_key_env = "NOT_SET_XYZ"
    monkeypatch.delenv("NOT_SET_XYZ", raising=False)
    result = hook.handle_event({"prompt": "how do I push"})
    assert result["skipped"] == "no Voyage API key"


def test_injects_hits_from_global_memory(seeded_cfg):
    result = hook.handle_event({"prompt": "how do I push my branch"})
    assert result["injected"] is True
    assert result["n_hits"] >= 1
    assert "Somnium: relevant memories" in result["text"]
    assert "graphite" in result["text"].lower() or "git push" in result["text"].lower()


def test_budget_truncates_low_priority_hits(seeded_cfg):
    seeded_cfg.context_injection.context_budget_tokens = 30  # ~120 chars
    result = hook.handle_event({"prompt": "how do I push"})
    # At least the top 1 hit fits even if below budget.
    assert result["injected"] is True
    assert result["n_hits"] >= 1


def test_nested_prompt_key(seeded_cfg):
    result = hook.handle_event({"hookInput": {"prompt": "how do I push"}})
    assert result["injected"] is True
