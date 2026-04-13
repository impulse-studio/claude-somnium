"""Embeddings providers.

Use ``get_embedder()`` to obtain the process-wide cached embedder.
The concrete class is selected by ``config.embeddings.provider``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import Embedder, EmbedResult
from .voyage import DEFAULT_EMBEDDING_DIM, VoyageEmbedder

if TYPE_CHECKING:
    from ..config import SomniumConfig

__all__ = [
    "DEFAULT_EMBEDDING_DIM",
    "EmbedResult",
    "Embedder",
    "VoyageEmbedder",
    "get_embedder",
]

_CACHED_EMBEDDER: Embedder | None = None


def get_embedder(config: SomniumConfig | None = None) -> Embedder:
    """Return a process-wide cached embedder.

    The provider is determined by ``config.embeddings.provider``.
    Passing a new *config* resets the cached instance.
    """
    global _CACHED_EMBEDDER  # noqa: PLW0603
    if _CACHED_EMBEDDER is None or config is not None:
        from ..config import get_config

        cfg = config or get_config()
        provider = cfg.embeddings.provider

        if provider == "ollama":
            from .ollama import OllamaEmbedder

            _CACHED_EMBEDDER = OllamaEmbedder(config=cfg)
        else:
            _CACHED_EMBEDDER = VoyageEmbedder(config=cfg)

    return _CACHED_EMBEDDER


def reset_embedder_cache() -> None:
    """Clear the cached embedder (useful in tests)."""
    global _CACHED_EMBEDDER  # noqa: PLW0603
    _CACHED_EMBEDDER = None
