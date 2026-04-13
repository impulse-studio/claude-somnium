"""Ollama embeddings provider.

Calls a local Ollama instance via its HTTP API. No API key needed —
embeddings are computed on-device, so cost is always $0.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx

from .base import KNOWN_MODELS, EmbedResult, dim_for_model

if TYPE_CHECKING:
    from ..config import SomniumConfig

from ..config import get_config


class OllamaEmbedder:
    """Embedding provider backed by a local Ollama server."""

    def __init__(self, config: SomniumConfig | None = None) -> None:
        self.config = config or get_config()
        self._base_url = self.config.embeddings.ollama_base_url.rstrip("/")
        self._client = httpx.Client(base_url=self._base_url, timeout=120.0)
        self._dim_cache: int | None = None

    # ------------------------------------------------------------------
    # Embedder protocol
    # ------------------------------------------------------------------

    @property
    def embedding_dim(self) -> int:
        """Return the dimension of the embedding vectors.

        Looks up the text model in the known-models catalog first.
        Falls back to probing the Ollama server with a tiny request.
        """
        if self._dim_cache is not None:
            return self._dim_cache

        model = self.model_for("text")
        known = dim_for_model(model)
        if known is not None:
            self._dim_cache = known
            return known

        # Auto-detect by embedding a short string
        result = self.embed(["dim probe"], kind="text", input_type="document")
        if result.embeddings:
            self._dim_cache = len(result.embeddings[0])
            return self._dim_cache

        msg = f"Could not determine embedding dimension for Ollama model {model!r}"
        raise RuntimeError(msg)

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
        """Embed a list of strings via the Ollama /api/embed endpoint."""
        model = self.model_for(kind)
        if not texts:
            return EmbedResult(embeddings=[], model=model, input_type=input_type)

        all_embeddings: list[list[float]] = []
        batch_size = max(1, self.config.embeddings.batch_size)

        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            data = self._request_with_retry(model, batch)
            all_embeddings.extend(data)

        from ..cost import log_cost

        log_cost(
            source="index",
            model=model,
            tokens=0,
            cost_usd=0.0,
            context=f"{len(texts)} texts, {input_type} (ollama local)",
        )

        return EmbedResult(embeddings=all_embeddings, model=model, input_type=input_type)

    def embed_query(self, text: str, *, kind: str = "text") -> list[float]:
        """Convenience: embed a single query string."""
        result = self.embed([text], kind=kind, input_type="query")
        return result.embeddings[0]

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _request_with_retry(
        self,
        model: str,
        texts: list[str],
        max_attempts: int = 5,
    ) -> list[list[float]]:
        """POST to /api/embed with exponential backoff retry."""
        attempt = 0
        while True:
            try:
                resp = self._client.post(
                    "/api/embed",
                    json={"model": model, "input": texts},
                )
                resp.raise_for_status()
                body: dict[str, Any] = resp.json()
                embeddings: list[list[float]] = body["embeddings"]
            except Exception as exc:
                attempt += 1
                if attempt >= max_attempts:
                    raise RuntimeError(
                        f"Ollama embedding failed after {attempt} attempts: {exc}"
                    ) from exc
                time.sleep(min(2**attempt, 16))
            else:
                return embeddings


# ------------------------------------------------------------------
# Ollama connectivity helpers (used by onboarding + status)
# ------------------------------------------------------------------


def check_ollama_running(base_url: str = "http://localhost:11434") -> bool:
    """Return True if Ollama is reachable at the given URL."""
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=5.0)
    except (httpx.ConnectError, httpx.TimeoutException, OSError):
        return False
    else:
        return resp.status_code == 200  # noqa: PLR2004


def list_ollama_models(base_url: str = "http://localhost:11434") -> list[str]:
    """Return model names available in the local Ollama instance.

    Only returns models that are known embedding models, plus any model
    whose name contains 'embed'.
    """
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=10.0)
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        models: list[str] = []
        for m in body.get("models", []):
            name: str = m.get("name", "")
            # Strip tag suffix (e.g. "nomic-embed-text:latest" → "nomic-embed-text")
            base_name = name.split(":", maxsplit=1)[0]
            if base_name in KNOWN_MODELS or "embed" in base_name.lower():
                models.append(base_name)
        return sorted(set(models))
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError, OSError):
        return []
