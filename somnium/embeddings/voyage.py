"""Voyage AI embeddings wrapper.

Handles batching, retries, and model selection. The rest of Somnium
should only talk to this module, never the voyageai SDK directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import voyageai

if TYPE_CHECKING:
    from ..config import SomniumConfig

from ..config import get_config

# voyage-3.5 and voyage-code-3 both default to 1024 dims.
DEFAULT_EMBEDDING_DIM = 1024


@dataclass
class EmbedResult:
    embeddings: list[list[float]]
    model: str
    input_type: str  # "document" or "query"


class VoyageEmbedder:
    """Thin wrapper around voyageai.Client.

    Phase 1 uses the same model for both text and code lookups via
    the `kind` parameter. Set `kind="code"` when embedding source code,
    otherwise `kind="text"`.
    """

    def __init__(self, config: SomniumConfig | None = None) -> None:
        self.config = config or get_config()
        api_key = self.config.embeddings.resolve_api_key()
        if not api_key:
            raise RuntimeError(
                "No Voyage API key found. Set VOYAGE_API_KEY or put "
                '`api_key = "..."` in [embeddings] of your config.toml.'
            )
        self._client = voyageai.Client(api_key=api_key)

    # ------------------------------------------------------------------

    def model_for(self, kind: str) -> str:
        if kind == "code":
            return self.config.embeddings.model_code
        return self.config.embeddings.model_text

    def embed(
        self,
        texts: list[str],
        *,
        kind: str = "text",
        input_type: str = "document",
    ) -> EmbedResult:
        """Embed a list of strings. Returns a list of float vectors."""
        if not texts:
            return EmbedResult(embeddings=[], model=self.model_for(kind), input_type=input_type)

        model = self.model_for(kind)
        batch_size = max(1, self.config.embeddings.batch_size)
        all_embeddings: list[list[float]] = []

        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            attempt = 0
            while True:
                try:
                    resp = self._client.embed(
                        texts=batch,
                        model=model,
                        input_type=input_type,
                    )
                    all_embeddings.extend(resp.embeddings)
                    break
                except Exception as exc:
                    attempt += 1
                    if attempt >= 5:  # noqa: PLR2004
                        raise RuntimeError(
                            f"Voyage embedding failed after {attempt} attempts: {exc}"
                        ) from exc
                    time.sleep(min(2**attempt, 16))

        return EmbedResult(embeddings=all_embeddings, model=model, input_type=input_type)

    def embed_query(self, text: str, *, kind: str = "text") -> list[float]:
        """Convenience helper for single-query embedding."""
        result = self.embed([text], kind=kind, input_type="query")
        return result.embeddings[0]


_CACHED_EMBEDDER: VoyageEmbedder | None = None


def get_embedder(config: SomniumConfig | None = None) -> VoyageEmbedder:
    """Return a process-wide cached embedder. Reset by calling with
    a fresh config object."""
    global _CACHED_EMBEDDER  # noqa: PLW0603
    if _CACHED_EMBEDDER is None or config is not None:
        _CACHED_EMBEDDER = VoyageEmbedder(config=config)
    return _CACHED_EMBEDDER
