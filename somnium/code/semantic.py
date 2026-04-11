"""Semantic code search query interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..config import SomniumConfig, get_config
from ..embeddings import get_embedder
from ..storage.parquet_store import ParquetStore
from .indexer import CODE_SCOPE

if TYPE_CHECKING:
    from ..storage.vector import SearchHit


@dataclass
class CodeSearchHit:
    file_path: str
    start_line: int | None
    end_line: int | None
    score: float
    text: str
    language: str

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "score": self.score,
            "text": self.text,
            "language": self.language,
        }


def _parse_hit(hit: SearchHit) -> CodeSearchHit:
    """Extract structured fields from a raw vector-store hit.

    The hit's `text` is the breadcrumb (from heading_path) followed by
    the raw code body. We split them back apart and parse the line
    range + language out of the breadcrumb.
    """
    # Split breadcrumb from body. MemoryChunk.display_text joins them with "\n\n".
    breadcrumb = ""
    body = hit.text
    if "\n\n" in hit.text:
        breadcrumb, _, body = hit.text.partition("\n\n")

    # Breadcrumb shape: "[lang] filename:start-end"
    language = ""
    start: int | None = None
    end: int | None = None
    if breadcrumb:
        if breadcrumb.startswith("["):
            close = breadcrumb.find("]")
            if close > 0:
                language = breadcrumb[1:close]
                breadcrumb = breadcrumb[close + 1 :].lstrip()
        if ":" in breadcrumb:
            _, _, rng = breadcrumb.rpartition(":")
            if "-" in rng:
                a, _, b = rng.partition("-")
                try:
                    start = int(a)
                    end = int(b)
                except ValueError:
                    pass

    return CodeSearchHit(
        file_path=hit.file_path,
        start_line=start,
        end_line=end,
        score=hit.score,
        text=body,
        language=language,
    )


def search_code(
    query: str,
    *,
    top_k: int = 5,
    config: SomniumConfig | None = None,
) -> list[CodeSearchHit]:
    """Run a semantic search on the per-project code index.

    Returns an empty list if no project is detected or the code index
    does not exist yet.
    """
    cfg = config or get_config()
    if cfg.project_code_index_path is None or not cfg.project_code_index_path.exists():
        return []

    embedder = get_embedder(cfg)
    query_vec = embedder.embed_query(query, kind="code")

    with ParquetStore(cfg.project_code_index_path) as store:
        raw_hits = store.search(query_vec, top_k=top_k, scopes=[CODE_SCOPE])

    return [_parse_hit(h) for h in raw_hits]
