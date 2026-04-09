"""Somnium MCP server.

Exposes Somnium's memory + (eventually) code search as MCP tools that
Claude Code can call. Runs over stdio, so registering it in
`~/.claude/settings.json` is just:

    "mcpServers": {
        "somnium": { "command": "somnium-mcp" }
    }

Phase 1 tools: memory_search, memory_write.
Phase 4 tools: code_search_symbolic, code_search_semantic.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import SomniumConfig, get_config
from .embeddings import get_embedder
from .indexer import index_single_file
from .storage.scope import Scope, normalize_scopes
from .storage.vector import SearchHit, VectorStore

mcp = FastMCP("somnium")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _global_store(config: SomniumConfig) -> VectorStore:
    return VectorStore(config.global_index_path)


def _project_store(config: SomniumConfig) -> VectorStore | None:
    if not config.project_index_path:
        return None
    return VectorStore(config.project_index_path)


def _search_all(
    query: str, top_k: int, scope: str, config: SomniumConfig
) -> list[SearchHit]:
    """Run the query against every relevant store and merge by score."""
    embedder = get_embedder(config)
    query_vec = embedder.embed_query(query)
    scopes = normalize_scopes(scope)

    hits: list[SearchHit] = []
    if config.global_index_path.exists():
        with _global_store(config) as store:
            hits.extend(store.search(query_vec, top_k=top_k, scopes=scopes))

    project_store = _project_store(config)
    if project_store is not None and config.project_index_path.exists():
        with project_store as store:
            hits.extend(store.search(query_vec, top_k=top_k, scopes=scopes))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\s-]", "", text).strip().lower()
    slug = re.sub(r"[\s-]+", "-", slug)
    return slug[:60] or "memory"


# ----------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------


@mcp.tool()
def memory_search(
    query: str,
    scope: str = "all",
    top_k: int = 5,
) -> str:
    """Semantic search across your Somnium memories and skills.

    Args:
      query: Natural language query. Example: "how do we deploy to prod".
      scope: One of "all", "global", "project", "skills".
      top_k: Number of results to return (default 5, max 20).

    Returns a JSON array of hits with file_path, score, scope, heading,
    and text.
    """
    top_k = max(1, min(int(top_k), 20))
    config = get_config()
    hits = _search_all(query=query, top_k=top_k, scope=scope, config=config)
    return json.dumps([h.to_dict() for h in hits], indent=2)


@mcp.tool()
def memory_write(
    content: str,
    scope: str = "global",
    title: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """Append a new memory as a markdown file and reindex it.

    Args:
      content: Markdown body of the memory.
      scope: "global" (default) or "project". Project writes require
             Claude Code to be running inside a detected project root.
      title: Optional human title. Used as H1 and to derive the filename.
      tags: Optional list of tags stored in frontmatter.

    Returns a JSON object describing where the memory was written.
    """
    config = get_config()
    scope_key = scope.lower()
    if scope_key == "project":
        if not config.project_memory_dir:
            raise ValueError(
                "No project detected; cannot write a project-scoped memory."
            )
        target_dir = config.project_memory_dir
        store_path = config.project_index_path
        kind = "memory_project"
    else:
        target_dir = config.global_memory_dir
        store_path = config.global_index_path
        kind = "memory_global"

    target_dir.mkdir(parents=True, exist_ok=True)

    now = dt.datetime.now()
    date_stamp = now.strftime("%Y-%m-%d")
    slug_base = _slugify(title or content.splitlines()[0] if content.strip() else "memory")
    filename = f"{date_stamp}-{slug_base}.md"
    target_path = target_dir / filename
    # Avoid collisions
    counter = 1
    while target_path.exists():
        target_path = target_dir / f"{date_stamp}-{slug_base}-{counter}.md"
        counter += 1

    fm_lines = [
        "---",
        f"created_at: {now.isoformat()}",
        f"scope: {scope_key}",
    ]
    if tags:
        fm_lines.append(f"tags: {json.dumps(tags)}")
    fm_lines.append("---")

    body_parts = ["\n".join(fm_lines), ""]
    if title:
        body_parts.append(f"# {title}\n")
    body_parts.append(content.strip() + "\n")
    target_path.write_text("\n".join(body_parts), encoding="utf-8")

    # Reindex this single file so it becomes searchable immediately.
    if store_path is not None:
        with VectorStore(store_path) as store:
            index_single_file(store=store, path=target_path, kind=kind, config=config)

    return json.dumps(
        {
            "written": str(target_path),
            "scope": scope_key,
            "tags": tags or [],
        },
        indent=2,
    )


@mcp.tool()
def code_search_semantic(query: str, top_k: int = 5) -> str:
    """Semantic search across the current project's indexed source code.

    Args:
      query: Natural language description of what you're looking for.
             Example: "where do we handle authentication".
      top_k: Number of results (default 5, max 20).

    Returns a JSON array of hits with file_path, start_line, end_line,
    score, language, and a preview of the matched code.

    Requires a prior `somnium index --code` in the project root.
    """
    from .code.semantic import search_code

    top_k = max(1, min(int(top_k), 20))
    config = get_config()
    hits = search_code(query, top_k=top_k, config=config)
    return json.dumps([h.to_dict() for h in hits], indent=2)


@mcp.tool()
def memory_status() -> str:
    """Quick health/status snapshot of the Somnium indexes."""
    config = get_config()
    out: dict = {
        "global_index": str(config.global_index_path),
        "global_index_exists": config.global_index_path.exists(),
        "project_root": str(config.project_root) if config.project_root else None,
        "project_index": (
            str(config.project_index_path) if config.project_index_path else None
        ),
        "project_index_exists": bool(
            config.project_index_path and config.project_index_path.exists()
        ),
        "voyage_key_set": config.embeddings.resolve_api_key() is not None,
        "dream_enabled": config.dream.enabled,
    }
    if config.global_index_path.exists():
        with _global_store(config) as store:
            out["global_stats"] = store.stats()
    if config.project_index_path and config.project_index_path.exists():
        with VectorStore(config.project_index_path) as store:
            out["project_stats"] = store.stats()
    return json.dumps(out, indent=2)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def main() -> None:
    """Entry point for the `somnium-mcp` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
