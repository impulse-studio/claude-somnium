"""Tests for the Ollama embeddings provider.

All HTTP calls are mocked via monkeypatching httpx — no real Ollama
server is needed. We exercise: connectivity checks, batching, retry,
model selection, embedding dim detection, and the model listing helper.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from somnium.config import SomniumConfig
from somnium.embeddings import get_embedder, reset_embedder_cache
from somnium.embeddings.base import EmbedResult
from somnium.embeddings.ollama import (
    OllamaEmbedder,
    check_ollama_running,
    list_ollama_models,
)


def _make_cfg(
    model_text: str = "nomic-embed-text",
    model_code: str = "nomic-embed-text",
    base_url: str = "http://localhost:11434",
) -> SomniumConfig:
    cfg = SomniumConfig()
    cfg.embeddings.provider = "ollama"
    cfg.embeddings.model_text = model_text
    cfg.embeddings.model_code = model_code
    cfg.embeddings.ollama_base_url = base_url
    cfg.embeddings.batch_size = 4
    return cfg


def _mock_httpx_post(embeddings: list[list[float]]):
    """Return a mock httpx response with the given embeddings."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {"embeddings": embeddings}
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------


def test_model_for_text():
    cfg = _make_cfg(model_text="nomic-embed-text", model_code="nomic-embed-text")
    with patch("somnium.embeddings.ollama.httpx.Client"):
        embedder = OllamaEmbedder(config=cfg)
    assert embedder.model_for("text") == "nomic-embed-text"
    assert embedder.model_for("anything") == "nomic-embed-text"


def test_model_for_code():
    cfg = _make_cfg(model_text="nomic-embed-text", model_code="mxbai-embed-large")
    with patch("somnium.embeddings.ollama.httpx.Client"):
        embedder = OllamaEmbedder(config=cfg)
    assert embedder.model_for("code") == "mxbai-embed-large"


# ---------------------------------------------------------------------------
# Embedding dim
# ---------------------------------------------------------------------------


def test_embedding_dim_from_known_model():
    cfg = _make_cfg(model_text="nomic-embed-text")
    with patch("somnium.embeddings.ollama.httpx.Client"):
        embedder = OllamaEmbedder(config=cfg)
    assert embedder.embedding_dim == 768


def test_embedding_dim_from_known_model_mxbai():
    cfg = _make_cfg(model_text="mxbai-embed-large")
    with patch("somnium.embeddings.ollama.httpx.Client"):
        embedder = OllamaEmbedder(config=cfg)
    assert embedder.embedding_dim == 1024


def test_embedding_dim_autodetect_unknown_model():
    cfg = _make_cfg(model_text="custom-model-xyz")
    mock_client = MagicMock()
    mock_client.post.return_value = _mock_httpx_post([[0.1, 0.2, 0.3]])
    with patch("somnium.embeddings.ollama.httpx.Client", return_value=mock_client):
        embedder = OllamaEmbedder(config=cfg)
        dim = embedder.embedding_dim
    assert dim == 3


# ---------------------------------------------------------------------------
# Embed
# ---------------------------------------------------------------------------


def test_embed_empty_list():
    cfg = _make_cfg()
    mock_client = MagicMock()
    with patch("somnium.embeddings.ollama.httpx.Client", return_value=mock_client):
        embedder = OllamaEmbedder(config=cfg)
        result = embedder.embed([])
    assert isinstance(result, EmbedResult)
    assert result.embeddings == []
    mock_client.post.assert_not_called()


def test_embed_single_batch():
    cfg = _make_cfg()
    mock_client = MagicMock()
    mock_client.post.return_value = _mock_httpx_post([[1.0, 2.0], [3.0, 4.0]])
    with patch("somnium.embeddings.ollama.httpx.Client", return_value=mock_client):
        embedder = OllamaEmbedder(config=cfg)
        result = embedder.embed(["hello", "world"])
    assert result.embeddings == [[1.0, 2.0], [3.0, 4.0]]
    assert result.model == "nomic-embed-text"
    mock_client.post.assert_called_once()


def test_embed_multiple_batches():
    """batch_size=4, 6 inputs → 2 batches (4, 2)."""
    cfg = _make_cfg()
    mock_client = MagicMock()

    def _fake_post(url, json=None, **kwargs):
        n = len(json["input"]) if json else 0
        return _mock_httpx_post([[float(i)] for i in range(n)])

    mock_client.post.side_effect = _fake_post
    with patch("somnium.embeddings.ollama.httpx.Client", return_value=mock_client):
        embedder = OllamaEmbedder(config=cfg)
        result = embedder.embed([f"text_{i}" for i in range(6)])
    assert len(result.embeddings) == 6
    assert mock_client.post.call_count == 2


def test_embed_query():
    cfg = _make_cfg()
    mock_client = MagicMock()
    mock_client.post.return_value = _mock_httpx_post([[0.1, 0.2]])
    with patch("somnium.embeddings.ollama.httpx.Client", return_value=mock_client):
        embedder = OllamaEmbedder(config=cfg)
        vec = embedder.embed_query("test query")
    assert vec == [0.1, 0.2]


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


def test_embed_retries_then_succeeds():
    cfg = _make_cfg()
    mock_client = MagicMock()
    mock_client.post.side_effect = [
        Exception("transient"),
        _mock_httpx_post([[1.0, 0.0]]),
    ]
    with patch("somnium.embeddings.ollama.httpx.Client", return_value=mock_client), \
         patch("somnium.embeddings.ollama.time.sleep"):
        embedder = OllamaEmbedder(config=cfg)
        result = embedder.embed(["x"])
    assert result.embeddings == [[1.0, 0.0]]
    assert mock_client.post.call_count == 2


def test_embed_gives_up_after_5_attempts():
    cfg = _make_cfg()
    mock_client = MagicMock()
    mock_client.post.side_effect = Exception("permanent")
    with patch("somnium.embeddings.ollama.httpx.Client", return_value=mock_client), \
         patch("somnium.embeddings.ollama.time.sleep"):
        embedder = OllamaEmbedder(config=cfg)
        with pytest.raises(RuntimeError, match="failed after"):
            embedder.embed(["x"])
    assert mock_client.post.call_count == 5


# ---------------------------------------------------------------------------
# Connectivity helpers
# ---------------------------------------------------------------------------


def test_check_ollama_running_success():
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    with patch("somnium.embeddings.ollama.httpx.get", return_value=resp):
        assert check_ollama_running() is True


def test_check_ollama_running_failure():
    with patch("somnium.embeddings.ollama.httpx.get", side_effect=httpx.ConnectError("")):
        assert check_ollama_running() is False


def test_list_ollama_models_with_embed_models():
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = {
        "models": [
            {"name": "nomic-embed-text:latest"},
            {"name": "llama3:latest"},
            {"name": "mxbai-embed-large:latest"},
            {"name": "custom-embed-thing:v1"},
        ]
    }
    resp.raise_for_status = MagicMock()
    with patch("somnium.embeddings.ollama.httpx.get", return_value=resp):
        models = list_ollama_models()
    assert "nomic-embed-text" in models
    assert "mxbai-embed-large" in models
    assert "custom-embed-thing" in models  # contains "embed"
    assert "llama3" not in models  # not an embedding model


def test_list_ollama_models_connection_error():
    with patch("somnium.embeddings.ollama.httpx.get", side_effect=httpx.ConnectError("")):
        assert list_ollama_models() == []


def test_list_ollama_models_http_status_error():
    with patch(
        "somnium.embeddings.ollama.httpx.get",
        side_effect=httpx.HTTPStatusError("500", request=MagicMock(), response=MagicMock()),
    ):
        assert list_ollama_models() == []


def test_check_ollama_running_timeout():
    with patch("somnium.embeddings.ollama.httpx.get", side_effect=httpx.TimeoutException("")):
        assert check_ollama_running() is False


def test_check_ollama_running_non_200():
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 500
    with patch("somnium.embeddings.ollama.httpx.get", return_value=resp):
        assert check_ollama_running() is False


def test_embedding_dim_autodetect_empty_result_raises():
    """When the probe returns empty embeddings, a RuntimeError is raised."""
    cfg = _make_cfg(model_text="custom-no-dim")
    mock_client = MagicMock()
    mock_client.post.return_value = _mock_httpx_post([])
    with patch("somnium.embeddings.ollama.httpx.Client", return_value=mock_client):
        embedder = OllamaEmbedder(config=cfg)
        with pytest.raises(RuntimeError, match="Could not determine"):
            _ = embedder.embedding_dim


def test_embedding_dim_is_cached():
    """Second access to embedding_dim should reuse cached value."""
    cfg = _make_cfg(model_text="nomic-embed-text")
    with patch("somnium.embeddings.ollama.httpx.Client"):
        embedder = OllamaEmbedder(config=cfg)
        dim1 = embedder.embedding_dim
        dim2 = embedder.embedding_dim
    assert dim1 == dim2 == 768


# ---------------------------------------------------------------------------
# Factory: get_embedder() Ollama branch
# ---------------------------------------------------------------------------


def test_get_embedder_returns_ollama_when_provider_is_ollama():
    """get_embedder() dispatches to OllamaEmbedder when provider=ollama."""
    cfg = _make_cfg()
    reset_embedder_cache()
    with patch("somnium.embeddings.ollama.httpx.Client"):
        embedder = get_embedder(config=cfg)
    assert isinstance(embedder, OllamaEmbedder)
    reset_embedder_cache()


def test_get_embedder_ollama_caches_instance():
    cfg = _make_cfg()
    reset_embedder_cache()
    with patch("somnium.embeddings.ollama.httpx.Client"):
        first = get_embedder(config=cfg)
        second = get_embedder()  # no arg → reuse
    assert first is second
    reset_embedder_cache()


def test_reset_embedder_cache_clears_instance():
    cfg = _make_cfg()
    reset_embedder_cache()
    with patch("somnium.embeddings.ollama.httpx.Client"):
        first = get_embedder(config=cfg)
        reset_embedder_cache()
        second = get_embedder(config=cfg)
    assert first is not second
    reset_embedder_cache()
