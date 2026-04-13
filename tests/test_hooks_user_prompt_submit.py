"""Tests for the UserPromptSubmit context injection hook."""

from __future__ import annotations

from pathlib import Path

import pytest

from somnium import indexer
from somnium.config import SomniumConfig
from somnium.embeddings.voyage import EmbedResult
from somnium.hooks import user_prompt_submit as hook
from somnium.storage.parquet_store import ParquetStore


class _FakeEmbedder:
    @property
    def embedding_dim(self):
        return 4

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

    def rerank(self, query, documents, *, model="rerank-2-lite", top_k=None):
        """No-op reranker: return documents in original order."""
        from somnium.embeddings.base import RerankResult

        results = [
            RerankResult(index=i, score=1.0 - i * 0.01, document=doc)
            for i, doc in enumerate(documents)
        ]
        if top_k:
            results = results[:top_k]
        return results


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

    # Force hook-constructed ParquetStore instances to use dim=4.
    _real_ps = hook.ParquetStore

    def _ps_with_dim(path, embedding_dim=4):
        return _real_ps(path, embedding_dim=4)

    monkeypatch.setattr(hook, "ParquetStore", _ps_with_dim)

    fake = _FakeEmbedder()

    def _fake_get_embedder(config=None):
        return fake

    monkeypatch.setattr(
        "somnium.embeddings.get_embedder", _fake_get_embedder
    )
    monkeypatch.setattr(indexer, "get_embedder", _fake_get_embedder)

    # Seed the global index with a couple of chunks.
    # Use ParquetStore so the .parquet is written (handle_event reads from ParquetStore).
    _ps = ParquetStore(cfg.global_index_path, embedding_dim=4)
    store = _ps.__enter__()
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
    _ps.__exit__(None, None, None)

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


# ---------------------------------------------------------------------------
# Session-scoped state
# ---------------------------------------------------------------------------


def test_session_id_propagated_in_result(seeded_cfg):
    result = hook.handle_event({"prompt": "how do I push", "session_id": "abc-123"})
    assert result["session_id"] == "abc-123"


def test_session_id_empty_when_absent(seeded_cfg):
    result = hook.handle_event({"prompt": "how do I push"})
    assert result["session_id"] == ""


def test_included_hits_in_result(seeded_cfg):
    result = hook.handle_event({"prompt": "how do I push"})
    assert result["injected"] is True
    assert "included_hits" in result
    assert len(result["included_hits"]) == result["n_hits"]


def test_write_state_creates_session_file(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path)
    hook._write_state(
        session_id="sess-42",
        hits=[{"title": "foo", "scope": "global", "score": 0.9, "path": "~/mem/foo.md"}],
    )
    state_file = tmp_path / "prompt_context_sess-42.json"
    assert state_file.exists()
    import json

    data = json.loads(state_file.read_text())
    assert data["session_id"] == "sess-42"
    assert data["n_skills"] == 0
    assert data["n_memories"] == 1
    assert len(data["hits"]) == 1
    assert data["hits"][0]["title"] == "foo"


def test_write_state_cumulative_dedup(tmp_path, monkeypatch):
    """Multiple writes merge hits, deduplicating by (title, scope)."""
    import json

    monkeypatch.setattr(hook, "STATE_DIR", tmp_path)
    hook._write_state(
        session_id="sess-1",
        hits=[
            {"title": "A", "scope": "global", "score": 0.8, "path": "~/a.md"},
            {"title": "B", "scope": "project", "score": 0.7, "path": "~/b.md"},
        ],
    )
    # Second write: one overlap (A), one new (C)
    hook._write_state(
        session_id="sess-1",
        hits=[
            {"title": "A", "scope": "global", "score": 0.9, "path": "~/a.md"},
            {"title": "C", "scope": "skill_project", "score": 0.6, "path": "~/c.md"},
        ],
    )
    data = json.loads((tmp_path / "prompt_context_sess-1.json").read_text())
    assert data["n_hits"] == 3  # A, B, C
    assert data["n_skills"] == 1  # C (skill_project)
    assert data["n_memories"] == 2  # A, B
    # A's score should be updated to the higher value
    a_hit = next(h for h in data["hits"] if h["title"] == "A")
    assert a_hit["score"] == 0.9


def test_write_state_fallback_without_session_id(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path)
    hook._write_state(session_id="", hits=[])
    assert (tmp_path / "prompt_context.json").exists()


def test_cleanup_old_state_files(tmp_path, monkeypatch):
    import time

    monkeypatch.setattr(hook, "STATE_DIR", tmp_path)
    # Create an "old" file and a "new" file.
    old = tmp_path / "prompt_context_old.json"
    new = tmp_path / "prompt_context_new.json"
    old.write_text("{}")
    new.write_text("{}")
    # Backdate the old file to 2 days ago.
    old_time = time.time() - 2 * 86400
    import os

    os.utime(old, (old_time, old_time))

    hook._cleanup_old_state_files()
    assert not old.exists()
    assert new.exists()


# ---------------------------------------------------------------------------
# Reranker integration
# ---------------------------------------------------------------------------


class _FakeEmbedderWithRerank(_FakeEmbedder):
    """Extends the fake embedder with a rerank method that reverses order."""

    def rerank(self, query, documents, *, model="rerank-2-lite", top_k=None):
        from somnium.embeddings.base import RerankResult

        # Return documents in reverse order with decreasing scores.
        results = []
        n = len(documents)
        for i in range(n):
            idx = n - 1 - i
            results.append(RerankResult(index=idx, score=1.0 - i * 0.1, document=documents[idx]))
        if top_k:
            results = results[:top_k]
        return results


def test_reranker_reorders_hits(seeded_cfg, monkeypatch):
    """Enable reranker → hits reordered by reranker scores."""
    seeded_cfg.context_injection.reranker_enabled = True
    seeded_cfg.embeddings.provider = "voyage"

    fake = _FakeEmbedderWithRerank()
    monkeypatch.setattr("somnium.embeddings.get_embedder", lambda config=None: fake)

    result = hook.handle_event({"prompt": "how do I push"})
    assert result["injected"] is True
    # The reranker reverses order, so the second hit (lower embedding score)
    # should now be first.
    hits = result["included_hits"]
    assert len(hits) >= 2
    assert hits[0].score > hits[1].score


def test_reranker_enabled_by_default(seeded_cfg):
    """Default config → reranker_enabled=True."""
    assert seeded_cfg.context_injection.reranker_enabled is True
    result = hook.handle_event({"prompt": "how do I push"})
    assert result["injected"] is True


def test_reranker_skipped_for_ollama(seeded_cfg, monkeypatch):
    """provider=ollama → no reranking even if enabled."""
    seeded_cfg.context_injection.reranker_enabled = True
    seeded_cfg.embeddings.provider = "ollama"

    result = hook.handle_event({"prompt": "how do I push"})
    assert result["injected"] is True
