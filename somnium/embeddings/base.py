"""Embedder protocol and shared types.

All embedding providers implement the ``Embedder`` protocol so the rest
of Somnium can work with any backend transparently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class EmbedResult:
    embeddings: list[list[float]]
    model: str
    input_type: str  # "document" or "query"


@dataclass
class RerankResult:
    index: int
    score: float
    document: str


@dataclass(frozen=True)
class ModelInfo:
    """Metadata for a known embedding model."""

    provider: str
    dim: int
    description: str


# ------------------------------------------------------------------
# Known models catalog — used by the onboarding wizard and for
# dimension lookup without making an API call.
# ------------------------------------------------------------------

KNOWN_MODELS: dict[str, ModelInfo] = {
    # Voyage AI (remote, API key required)
    "voyage-3.5": ModelInfo(
        provider="voyage",
        dim=1024,
        description="Voyage general-purpose",
    ),
    "voyage-code-3": ModelInfo(
        provider="voyage",
        dim=1024,
        description="Voyage code-optimized",
    ),
    "voyage-3.5-lite": ModelInfo(
        provider="voyage",
        dim=512,
        description="Voyage lightweight",
    ),
    # Ollama (local, free)  # noqa: ERA001
    "nomic-embed-text": ModelInfo(
        provider="ollama",
        dim=768,
        description="Nomic general-purpose (local)",
    ),
    "mxbai-embed-large": ModelInfo(
        provider="ollama",
        dim=1024,
        description="MixedBread large (local)",
    ),
    "all-minilm": ModelInfo(
        provider="ollama",
        dim=384,
        description="MiniLM lightweight (local)",
    ),
    "snowflake-arctic-embed": ModelInfo(
        provider="ollama",
        dim=1024,
        description="Snowflake Arctic (local)",
    ),
}


def dim_for_model(model: str) -> int | None:
    """Return the embedding dimension for a known model, or None."""
    info = KNOWN_MODELS.get(model)
    return info.dim if info else None


def models_for_provider(provider: str) -> dict[str, ModelInfo]:
    """Return all known models for a given provider."""
    return {k: v for k, v in KNOWN_MODELS.items() if v.provider == provider}


@runtime_checkable
class Embedder(Protocol):
    """Protocol that all embedding providers must satisfy."""

    @property
    def embedding_dim(self) -> int:
        """Dimensionality of the vectors produced by this embedder."""
        ...

    def model_for(self, kind: str) -> str:
        """Return the model name used for *kind* (``"text"`` or ``"code"``)."""
        ...

    def embed(
        self,
        texts: list[str],
        *,
        kind: str = "text",
        input_type: str = "document",
    ) -> EmbedResult:
        """Embed a list of strings. Returns vectors + metadata."""
        ...

    def embed_query(self, text: str, *, kind: str = "text") -> list[float]:
        """Convenience: embed a single query string."""
        ...
