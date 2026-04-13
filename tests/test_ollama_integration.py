"""Integration test: Ollama provider full flow (write → index → search).

All HTTP calls are mocked — no real Ollama server. This test exercises
the complete path from config → embedder → indexer → vector store → search
with the Ollama provider, ensuring the pieces fit together end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from somnium.config import SomniumConfig
from somnium.embeddings import get_embedder, reset_embedder_cache
from somnium.embeddings.ollama import OllamaEmbedder
from somnium.indexer import index_directory
from somnium.storage.parquet_store import ParquetStore


def _make_ollama_cfg(tmp_path: Path) -> SomniumConfig:
    """Create a SomniumConfig pointing at tmp_path with ollama provider."""
    global_root = tmp_path / "somnium_home"
    for sub in ("memory", "skills"):
        (global_root / sub).mkdir(parents=True)

    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    (project / ".claude" / "somnium" / "memory").mkdir(parents=True)

    cfg = SomniumConfig()
    cfg.storage.global_root = str(global_root)
    cfg.embeddings.provider = "ollama"
    cfg.embeddings.model_text = "nomic-embed-text"
    cfg.embeddings.model_code = "nomic-embed-text"
    cfg.embeddings.ollama_base_url = "http://localhost:11434"
    cfg.embeddings.batch_size = 4
    cfg.project_root = project
    return cfg


def _fake_ollama_response(embeddings: list[list[float]]):
    """Return a mock httpx.Response for the /api/embed endpoint."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"embeddings": embeddings}
    resp.raise_for_status = MagicMock()
    return resp


def test_ollama_full_flow_write_index_search(tmp_path, monkeypatch):
    """Full flow: seed memory → index with Ollama embeddings → search."""
    cfg = _make_ollama_cfg(tmp_path)

    # Fake 768-dim embeddings (nomic-embed-text)
    dim = 768
    base_vec = [0.0] * dim
    # Give the memory file a distinct vector so search finds it.
    mem_vec = list(base_vec)
    mem_vec[0] = 1.0
    query_vec = list(mem_vec)  # same direction → high similarity

    mock_client = MagicMock()

    call_count = {"n": 0}

    def _fake_post(url, json=None, **kwargs):
        call_count["n"] += 1
        if json:
            n = len(json["input"])
            return _fake_ollama_response([mem_vec for _ in range(n)])
        return _fake_ollama_response([])

    mock_client.post.side_effect = _fake_post

    with patch("somnium.embeddings.ollama.httpx.Client", return_value=mock_client):
        reset_embedder_cache()
        embedder = get_embedder(config=cfg)

        assert isinstance(embedder, OllamaEmbedder)
        assert embedder.embedding_dim == dim

        # Seed a memory file
        mem_dir = Path(cfg.storage.global_root) / "memory"
        (mem_dir / "test-memory.md").write_text(
            "---\ntags: [test]\n---\n\n"
            "# Test Memory\n\n"
            "This is a test memory for the Ollama integration flow.\n",
            encoding="utf-8",
        )

        # Index
        index_path = Path(cfg.storage.global_root) / "index.parquet"
        with ParquetStore(index_path, embedding_dim=dim) as store:
            stats = index_directory(
                store=store,
                directory=mem_dir,
                kind="memory_global",
                config=cfg,
            )
            assert stats.files_embedded >= 1

            # Search
            hits = store.search(query_vec, top_k=5)
            assert len(hits) >= 1
            assert hits[0].score > 0.5
            assert "test-memory" in hits[0].file_path

    reset_embedder_cache()


def test_ollama_mcp_memory_write_and_search(tmp_path, monkeypatch):
    """MCP server memory_write + memory_search with Ollama provider."""
    cfg = _make_ollama_cfg(tmp_path)

    dim = 768
    vec = [0.0] * dim
    vec[0] = 1.0

    mock_client = MagicMock()

    def _fake_post(url, json=None, **kwargs):
        n = len(json["input"]) if json else 0
        return _fake_ollama_response([vec for _ in range(n)])

    mock_client.post.side_effect = _fake_post

    from somnium import indexer as memory_indexer
    from somnium import mcp_server

    fake_embedder = MagicMock(spec=OllamaEmbedder)
    fake_embedder.embedding_dim = dim
    fake_embedder.embed.side_effect = (
        lambda texts, **kw: MagicMock(embeddings=[vec for _ in texts])
    )
    fake_embedder.embed_query.return_value = vec
    fake_embedder.model_for.return_value = "nomic-embed-text"

    monkeypatch.setattr(mcp_server, "get_config", lambda: cfg)
    monkeypatch.setattr(mcp_server, "get_embedder", lambda config=None: fake_embedder)
    monkeypatch.setattr(memory_indexer, "get_embedder", lambda c=None: fake_embedder)

    real_ps = mcp_server.ParquetStore

    def _ps(path, embedding_dim=dim):
        return real_ps(path, embedding_dim=dim)

    monkeypatch.setattr(mcp_server, "ParquetStore", _ps)

    # Write a memory
    result_json = mcp_server.memory_write(
        content="Use Ollama for local embeddings",
        scope="global",
        title="Ollama tip",
        tags=["ollama"],
    )
    result = json.loads(result_json)
    assert result["scope"] == "global"
    assert result["tags"] == ["ollama"]
    assert Path(result["written"]).exists()

    # Search for it
    raw = mcp_server.memory_search(query="ollama embeddings", scope="global", top_k=5)
    hits = json.loads(raw)
    assert isinstance(hits, list)
    assert len(hits) >= 1
    assert any("ollama" in h.get("file_path", "").lower() for h in hits)
