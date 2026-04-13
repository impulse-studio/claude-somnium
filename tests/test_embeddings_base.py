"""Tests for the embeddings base module: Protocol, catalog, helpers."""

from __future__ import annotations

from somnium.embeddings.base import (
    KNOWN_MODELS,
    Embedder,
    EmbedResult,
    ModelInfo,
    dim_for_model,
    models_for_provider,
)

# ---------------------------------------------------------------------------
# KNOWN_MODELS catalog
# ---------------------------------------------------------------------------


def test_known_models_has_voyage_entries():
    assert "voyage-3.5" in KNOWN_MODELS
    assert "voyage-code-3" in KNOWN_MODELS
    assert KNOWN_MODELS["voyage-3.5"].provider == "voyage"


def test_known_models_has_ollama_entries():
    assert "nomic-embed-text" in KNOWN_MODELS
    assert "mxbai-embed-large" in KNOWN_MODELS
    assert KNOWN_MODELS["nomic-embed-text"].provider == "ollama"


def test_dim_for_known_model():
    assert dim_for_model("voyage-3.5") == 1024
    assert dim_for_model("nomic-embed-text") == 768
    assert dim_for_model("all-minilm") == 384


def test_dim_for_unknown_model_returns_none():
    assert dim_for_model("unknown-model-xyz") is None


def test_models_for_provider_voyage():
    voyage = models_for_provider("voyage")
    assert all(v.provider == "voyage" for v in voyage.values())
    assert "voyage-3.5" in voyage


def test_models_for_provider_ollama():
    ollama = models_for_provider("ollama")
    assert all(v.provider == "ollama" for v in ollama.values())
    assert "nomic-embed-text" in ollama


def test_models_for_unknown_provider_empty():
    assert models_for_provider("nonexistent") == {}


# ---------------------------------------------------------------------------
# ModelInfo
# ---------------------------------------------------------------------------


def test_model_info_is_frozen():
    info = ModelInfo(provider="test", dim=128, description="test model")
    assert info.provider == "test"
    assert info.dim == 128


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    """Minimal conformant implementation for testing."""

    @property
    def embedding_dim(self) -> int:
        return 4

    def model_for(self, kind: str) -> str:
        return "fake"

    def embed(
        self,
        texts: list[str],
        *,
        kind: str = "text",
        input_type: str = "document",
    ) -> EmbedResult:
        return EmbedResult(
            embeddings=[[0.0] * 4 for _ in texts],
            model="fake",
            input_type=input_type,
        )

    def embed_query(self, text: str, *, kind: str = "text") -> list[float]:
        return [0.0] * 4


def test_fake_embedder_satisfies_protocol():
    embedder = _FakeEmbedder()
    assert isinstance(embedder, Embedder)


def test_embed_result_dataclass():
    r = EmbedResult(embeddings=[[1.0]], model="m", input_type="document")
    assert r.embeddings == [[1.0]]
    assert r.model == "m"
