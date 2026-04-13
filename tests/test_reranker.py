"""Tests for the Voyage reranker integration in VoyageEmbedder."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from somnium.config import SomniumConfig
from somnium.embeddings.base import RerankResult

# ---------------------------------------------------------------------------
# Fake Voyage SDK objects
# ---------------------------------------------------------------------------


@dataclass
class _FakeRerankingResult:
    index: int
    document: str
    relevance_score: float


@dataclass
class _FakeRerankingObject:
    results: list[_FakeRerankingResult]
    total_tokens: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_embedder(monkeypatch):
    """Build a VoyageEmbedder with a mocked voyageai.Client."""
    cfg = SomniumConfig()
    cfg.embeddings.api_key = "fake-key"

    mock_client = MagicMock()

    with patch("voyageai.Client", return_value=mock_client):
        from somnium.embeddings.voyage import VoyageEmbedder

        embedder = VoyageEmbedder(config=cfg)

    # Redirect cost logging to /dev/null.
    monkeypatch.setenv("SOMNIUM_HOME", "/tmp/somnium-test-reranker")
    return embedder, mock_client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_rerank_basic(monkeypatch):
    """Mock client.rerank, verify results ordered by score."""
    embedder, mock_client = _make_embedder(monkeypatch)
    mock_client.rerank.return_value = _FakeRerankingObject(
        results=[
            _FakeRerankingResult(index=1, document="B", relevance_score=0.9),
            _FakeRerankingResult(index=0, document="A", relevance_score=0.5),
        ],
        total_tokens=100,
    )

    results = embedder.rerank("query", ["A", "B"])
    assert len(results) == 2
    assert results[0].score == 0.9
    assert results[0].document == "B"
    assert results[1].score == 0.5
    assert results[1].document == "A"


def test_rerank_empty_documents(monkeypatch):
    """Empty list → []."""
    embedder, _ = _make_embedder(monkeypatch)
    results = embedder.rerank("query", [])
    assert results == []


def test_rerank_retries_on_failure(monkeypatch):
    """First call fails, second succeeds."""
    embedder, mock_client = _make_embedder(monkeypatch)
    monkeypatch.setattr("somnium.embeddings.voyage.time.sleep", lambda _: None)

    mock_client.rerank.side_effect = [
        RuntimeError("transient"),
        _FakeRerankingObject(
            results=[_FakeRerankingResult(index=0, document="A", relevance_score=0.8)],
            total_tokens=50,
        ),
    ]

    results = embedder.rerank("query", ["A"])
    assert len(results) == 1
    assert results[0].score == 0.8
    assert mock_client.rerank.call_count == 2


def test_rerank_logs_cost(monkeypatch, tmp_path):
    """log_cost called with source='rerank'."""
    embedder, mock_client = _make_embedder(monkeypatch)
    monkeypatch.setenv("SOMNIUM_HOME", str(tmp_path))

    mock_client.rerank.return_value = _FakeRerankingObject(
        results=[_FakeRerankingResult(index=0, document="A", relevance_score=0.7)],
        total_tokens=200,
    )

    embedder.rerank("query", ["A"])

    cost_file = tmp_path / "costs.jsonl"
    assert cost_file.exists()
    import json

    entry = json.loads(cost_file.read_text().strip())
    assert entry["source"] == "rerank"
    assert entry["model"] == "rerank-2-lite"
    assert entry["tokens"] == 200


def test_rerank_respects_top_k(monkeypatch):
    """Only top_k results returned when passed to the API."""
    embedder, mock_client = _make_embedder(monkeypatch)
    mock_client.rerank.return_value = _FakeRerankingObject(
        results=[_FakeRerankingResult(index=0, document="A", relevance_score=0.9)],
        total_tokens=50,
    )

    embedder.rerank("query", ["A", "B", "C"], top_k=1)
    call_kwargs = mock_client.rerank.call_args
    assert call_kwargs.kwargs["top_k"] == 1


def test_rerank_result_dataclass():
    """RerankResult fields accessible."""
    r = RerankResult(index=2, score=0.85, document="hello")
    assert r.index == 2
    assert r.score == 0.85
    assert r.document == "hello"


def test_rerank_passes_model_name(monkeypatch):
    """Correct model sent to API."""
    embedder, mock_client = _make_embedder(monkeypatch)
    mock_client.rerank.return_value = _FakeRerankingObject(
        results=[], total_tokens=0,
    )

    embedder.rerank("query", ["A"], model="rerank-2")
    call_kwargs = mock_client.rerank.call_args
    assert call_kwargs.kwargs["model"] == "rerank-2"


def test_rerank_max_retries_raises(monkeypatch):
    """5 failures → RuntimeError."""
    embedder, mock_client = _make_embedder(monkeypatch)
    monkeypatch.setattr("somnium.embeddings.voyage.time.sleep", lambda _: None)

    mock_client.rerank.side_effect = RuntimeError("always fails")

    with pytest.raises(RuntimeError, match="5 attempts"):
        embedder.rerank("query", ["A"])

    assert mock_client.rerank.call_count == 5
