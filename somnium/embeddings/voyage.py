"""Voyage AI embeddings wrapper.

Handles batching, retries, and model selection. The rest of Somnium
should only talk to this module, never the voyageai SDK directly.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import voyageai

from .base import EmbedResult, dim_for_model

if TYPE_CHECKING:
    from ..config import SomniumConfig

from ..config import get_config

# Kept for backwards-compat with tests that import it directly.
DEFAULT_EMBEDDING_DIM = 1024


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

    @property
    def embedding_dim(self) -> int:
        """Dimensionality of the vectors produced by this embedder."""
        known = dim_for_model(self.model_for("text"))
        return known if known is not None else DEFAULT_EMBEDDING_DIM

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

        total_tokens = 0
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
                    total_tokens += resp.total_tokens
                    break
                except Exception as exc:
                    attempt += 1
                    if attempt >= 5:  # noqa: PLR2004
                        raise RuntimeError(
                            f"Voyage embedding failed after {attempt} attempts: {exc}"
                        ) from exc
                    time.sleep(min(2**attempt, 16))

        from ..cost import log_cost, voyage_cost

        log_cost(
            source="index",
            model=model,
            tokens=total_tokens,
            cost_usd=voyage_cost(model, total_tokens),
            context=f"{len(texts)} texts, {input_type}",
        )

        return EmbedResult(embeddings=all_embeddings, model=model, input_type=input_type)

    def embed_query(self, text: str, *, kind: str = "text") -> list[float]:
        """Convenience helper for single-query embedding."""
        result = self.embed([text], kind=kind, input_type="query")
        return result.embeddings[0]
