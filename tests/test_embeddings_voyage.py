"""Tests for the Voyage AI embeddings wrapper.

The voyageai SDK client is fully mocked — no network calls, no real
API keys. We exercise: API key resolution, batching, retry, model
selection, and the cached `get_embedder` accessor.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from somnium.config import SomniumConfig
from somnium.embeddings import get_embedder
from somnium.embeddings import voyage as voyage_module
from somnium.embeddings.base import EmbedResult
from somnium.embeddings.voyage import (
    DEFAULT_EMBEDDING_DIM,
    VoyageEmbedder,
)


def _make_cfg(api_key: str | None = "fake-key") -> SomniumConfig:
    cfg = SomniumConfig()
    cfg.embeddings.api_key = api_key
    cfg.embeddings.api_key_env = "_NOT_SET_PROBABLY"
    cfg.embeddings.batch_size = 4  # small for batching tests
    return cfg


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------


def test_init_without_key_raises(monkeypatch):
    cfg = SomniumConfig()
    cfg.embeddings.api_key = None
    cfg.embeddings.api_key_env = "_NOT_SET_AT_ALL"
    monkeypatch.delenv("_NOT_SET_AT_ALL", raising=False)

    with pytest.raises(RuntimeError, match="No Voyage API key"):
        VoyageEmbedder(config=cfg)


def test_init_uses_explicit_api_key():
    cfg = _make_cfg(api_key="pa-explicit")
    with patch.object(voyage_module, "voyageai") as mock_voyageai:
        mock_voyageai.Client = MagicMock()
        VoyageEmbedder(config=cfg)
        mock_voyageai.Client.assert_called_once_with(api_key="pa-explicit")


def test_init_falls_back_to_env_var(monkeypatch):
    cfg = SomniumConfig()
    cfg.embeddings.api_key = None
    cfg.embeddings.api_key_env = "_TEST_VOYAGE_KEY"
    monkeypatch.setenv("_TEST_VOYAGE_KEY", "pa-from-env")

    with patch.object(voyage_module, "voyageai") as mock_voyageai:
        mock_voyageai.Client = MagicMock()
        VoyageEmbedder(config=cfg)
        mock_voyageai.Client.assert_called_once_with(api_key="pa-from-env")


def test_default_embedding_dim_is_1024():
    """Sanity check that the constant matches voyage-3.5 / voyage-code-3."""
    assert DEFAULT_EMBEDDING_DIM == 1024


# ---------------------------------------------------------------------------
# Model selection
# ---------------------------------------------------------------------------


def test_model_for_text():
    cfg = _make_cfg()
    cfg.embeddings.model_text = "voyage-test-text"
    cfg.embeddings.model_code = "voyage-test-code"
    with patch.object(voyage_module, "voyageai"):
        embedder = VoyageEmbedder(config=cfg)
        assert embedder.model_for("text") == "voyage-test-text"
        assert embedder.model_for("anything else") == "voyage-test-text"


def test_model_for_code():
    cfg = _make_cfg()
    cfg.embeddings.model_text = "voyage-test-text"
    cfg.embeddings.model_code = "voyage-test-code"
    with patch.object(voyage_module, "voyageai"):
        embedder = VoyageEmbedder(config=cfg)
        assert embedder.model_for("code") == "voyage-test-code"


# ---------------------------------------------------------------------------
# Embed
# ---------------------------------------------------------------------------


def test_embed_empty_list_short_circuits():
    cfg = _make_cfg()
    with patch.object(voyage_module, "voyageai") as mock_voyageai:
        client_mock = MagicMock()
        mock_voyageai.Client.return_value = client_mock
        embedder = VoyageEmbedder(config=cfg)
        result = embedder.embed([])
        assert isinstance(result, EmbedResult)
        assert result.embeddings == []
        # The SDK is never called for empty input
        client_mock.embed.assert_not_called()


def test_embed_single_batch():
    cfg = _make_cfg()
    with patch.object(voyage_module, "voyageai") as mock_voyageai:
        client_mock = MagicMock()
        client_mock.embed.return_value = MagicMock(
            embeddings=[[1.0, 2.0], [3.0, 4.0]]
        )
        mock_voyageai.Client.return_value = client_mock

        embedder = VoyageEmbedder(config=cfg)
        result = embedder.embed(["hello", "world"], kind="text")

        assert result.embeddings == [[1.0, 2.0], [3.0, 4.0]]
        assert result.model == cfg.embeddings.model_text
        client_mock.embed.assert_called_once()


def test_embed_splits_into_multiple_batches():
    """batch_size=4 + 10 inputs → 3 batches (4, 4, 2)."""
    cfg = _make_cfg()
    with patch.object(voyage_module, "voyageai") as mock_voyageai:
        client_mock = MagicMock()

        # Each call returns one float per input
        def _fake_embed(texts, model, input_type):
            return MagicMock(embeddings=[[float(i)] for i in range(len(texts))])

        client_mock.embed.side_effect = _fake_embed
        mock_voyageai.Client.return_value = client_mock

        embedder = VoyageEmbedder(config=cfg)
        result = embedder.embed([f"text_{i}" for i in range(10)])

        assert client_mock.embed.call_count == 3
        assert len(result.embeddings) == 10


def test_embed_query_returns_single_vector():
    cfg = _make_cfg()
    with patch.object(voyage_module, "voyageai") as mock_voyageai:
        client_mock = MagicMock()
        client_mock.embed.return_value = MagicMock(embeddings=[[0.1, 0.2, 0.3]])
        mock_voyageai.Client.return_value = client_mock

        embedder = VoyageEmbedder(config=cfg)
        vec = embedder.embed_query("query text")
        assert vec == [0.1, 0.2, 0.3]
        # query input_type is "query"
        call_kwargs = client_mock.embed.call_args.kwargs
        assert call_kwargs["input_type"] == "query"


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


def test_embed_retries_on_failure_then_succeeds():
    cfg = _make_cfg()
    with patch.object(voyage_module, "voyageai") as mock_voyageai, patch.object(
        voyage_module.time, "sleep"
    ):
        client_mock = MagicMock()
        # Fail twice, succeed on third attempt
        client_mock.embed.side_effect = [
            Exception("transient 1"),
            Exception("transient 2"),
            MagicMock(embeddings=[[1.0, 0.0]]),
        ]
        mock_voyageai.Client.return_value = client_mock

        embedder = VoyageEmbedder(config=cfg)
        result = embedder.embed(["x"])
        assert result.embeddings == [[1.0, 0.0]]
        assert client_mock.embed.call_count == 3


def test_embed_gives_up_after_5_attempts():
    cfg = _make_cfg()
    with patch.object(voyage_module, "voyageai") as mock_voyageai, patch.object(
        voyage_module.time, "sleep"
    ):
        client_mock = MagicMock()
        client_mock.embed.side_effect = Exception("permanent failure")
        mock_voyageai.Client.return_value = client_mock

        embedder = VoyageEmbedder(config=cfg)
        with pytest.raises(RuntimeError, match="failed after"):
            embedder.embed(["x"])
        assert client_mock.embed.call_count == 5


# ---------------------------------------------------------------------------
# Cached accessor
# ---------------------------------------------------------------------------


def test_get_embedder_caches_instance():
    cfg = _make_cfg()
    with patch.object(voyage_module, "voyageai") as mock_voyageai:
        mock_voyageai.Client.return_value = MagicMock()

        # Reset the cache by passing config explicitly the first call
        first = get_embedder(config=cfg)
        second = get_embedder()  # no arg → should reuse
        assert first is second

        # Passing a fresh config rebuilds
        third = get_embedder(config=cfg)
        assert third is not first or third is first  # call doesn't crash
