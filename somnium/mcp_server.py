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
from typing import TYPE_CHECKING

import frontmatter
from mcp.server.fastmcp import FastMCP

if TYPE_CHECKING:
    from .config import SomniumConfig
    from .storage.vector import SearchHit

from .config import get_config
from .dream.router import _find_similar_slug
from .embeddings import get_embedder
from .indexer import index_single_file
from .storage.parquet_store import ParquetStore
from .storage.scope import normalize_scopes

mcp = FastMCP("somnium")


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _global_store(config: SomniumConfig) -> ParquetStore:
    return ParquetStore(config.global_index_path)


def _project_store(config: SomniumConfig) -> ParquetStore | None:
    if not config.project_index_path:
        return None
    return ParquetStore(config.project_index_path)


def _search_all(
    query: str,
    top_k: int,
    scope: str,
    config: SomniumConfig,
    tags: list[str] | None = None,
) -> list[SearchHit]:
    """Run the query against every relevant store and merge by score."""
    embedder = get_embedder(config)
    query_vec = embedder.embed_query(query)
    scopes = normalize_scopes(scope)

    hits: list[SearchHit] = []
    if config.global_index_path.exists():
        with _global_store(config) as store:
            hits.extend(store.search(query_vec, top_k=top_k, scopes=scopes, tags=tags))

    project_store = _project_store(config)
    if project_store is not None and config.project_index_path.exists():
        with project_store as store:
            hits.extend(store.search(query_vec, top_k=top_k, scopes=scopes, tags=tags))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\s-]", "", text).strip().lower()
    slug = re.sub(r"[\s-]+", "-", slug)
    return slug[:60] or "memory"


# ----------------------------------------------------------------------
# Session state tracking
# ----------------------------------------------------------------------


def _find_latest_state_file() -> Path | None:
    """Find the most recently modified session state file."""
    state_dir = Path.home() / ".claude" / "somnium" / "state"
    if not state_dir.exists():
        return None
    candidates = sorted(
        state_dir.glob("prompt_context_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _track_mcp_hits(hits: list[SearchHit]) -> None:
    """Append MCP memory_search results to the most recent session state
    so /somnium:context shows them alongside auto-injected memories."""
    if not hits:
        return
    try:
        state_file = _find_latest_state_file()
        if not state_file:
            return
        existing = json.loads(state_file.read_text(encoding="utf-8"))
        existing_hits: list[dict] = existing.get("hits", [])

        seen: dict[tuple[str, str], int] = {}
        for i, h in enumerate(existing_hits):
            seen[(str(h.get("title", "")), str(h.get("scope", "")))] = i

        merged = list(existing_hits)
        for hit in hits:
            title = hit.heading_path[-1] if hit.heading_path else Path(hit.file_path).stem
            home = Path.home()
            try:
                short = f"~/{Path(hit.file_path).resolve().relative_to(home)}"
            except ValueError:
                short = hit.file_path
            key = (title, hit.scope)
            if key in seen:
                idx = seen[key]
                if hit.score > float(merged[idx].get("score", 0)):
                    merged[idx]["score"] = round(hit.score, 3)
            else:
                seen[key] = len(merged)
                merged.append({
                    "title": title,
                    "scope": hit.scope,
                    "score": round(hit.score, 3),
                    "path": short,
                    "source": "search",
                })

        n_skills = sum(1 for h in merged if "skill" in str(h.get("scope", "")))
        n_memories = len(merged) - n_skills
        existing["hits"] = merged
        existing["n_hits"] = len(merged)
        existing["n_skills"] = n_skills
        existing["n_memories"] = n_memories
        existing["timestamp"] = dt.datetime.now(tz=dt.UTC).isoformat()
        state_file.write_text(json.dumps(existing), encoding="utf-8")
    except Exception:  # noqa: S110, BLE001
        pass


# ----------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------


@mcp.tool()
def memory_search(
    query: str,
    scope: str = "all",
    top_k: int = 5,
    tags: list[str] | None = None,
) -> str:
    """Semantic search across your Somnium memories and skills.

    Args:
      query: Natural language query. Example: "how do we deploy to prod".
      scope: One of "all", "global", "project", "skills".
      top_k: Number of results to return (default 5, max 20).
      tags: Optional list of tags to filter by. Only memories with at
            least one matching tag are returned.

    Returns a JSON array of hits with file_path, score, scope, heading,
    and text.
    """
    top_k = max(1, min(int(top_k), 20))
    config = get_config()
    hits = _search_all(query=query, top_k=top_k, scope=scope, tags=tags, config=config)
    # Track search results in session state so /somnium:context shows them.
    _track_mcp_hits(hits)
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

    now = dt.datetime.now(tz=dt.UTC)
    slug_base = _slugify(title or (content.splitlines()[0] if content.strip() else "memory"))
    slug_base = _find_similar_slug(slug_base, target_dir)
    target_path = target_dir / f"{slug_base}.md"

    # Preserve original `created_at` if the file already exists, so we
    # can update memories in place rather than always creating new ones.
    # PyYAML deserializes date-shaped strings into datetime objects, so
    # we re-serialize via isoformat() for a stable round-trip.
    created_at = now.isoformat()
    if target_path.exists():
        try:
            existing = frontmatter.loads(target_path.read_text(encoding="utf-8"))
            existing_value = existing.metadata.get("created_at")
            if isinstance(existing_value, dt.datetime | dt.date):
                created_at = existing_value.isoformat()
            elif existing_value is not None:
                created_at = str(existing_value)
        except Exception:  # noqa: S110
            pass

    fm_lines = [
        "---",
        f"created_at: {created_at}",
        f"updated_at: {now.isoformat()}",
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
        with ParquetStore(store_path) as store:
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
def injection_debug(session_id: str = "") -> str:
    """List all memories and skills Somnium injected in this session.

    Shows cumulative data: every unique memory/skill auto-injected by
    the UserPromptSubmit hook plus any retrieved via memory_search.

    Args:
      session_id: Claude Code session ID. If empty, reads the most
                  recently modified state file.

    Returns a JSON object with timestamp, counts, and the full hits
    array (title, scope, score, path, source) for each item.
    """
    state_dir = Path.home() / ".claude" / "somnium" / "state"
    state_file: Path | None = None

    if session_id:
        candidate = state_dir / f"prompt_context_{session_id}.json"
        if candidate.exists():
            state_file = candidate

    if not state_file:
        state_file = _find_latest_state_file()

    if not state_file or not state_file.exists():
        return json.dumps({"error": "No injection state found. No memories were injected yet."})

    data = json.loads(state_file.read_text(encoding="utf-8"))
    return json.dumps(data, indent=2)


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
        with ParquetStore(config.project_index_path) as store:
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
