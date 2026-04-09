"""PostToolUse hook: incremental reindex on Write/Edit/MultiEdit.

When Claude Code writes or edits a file, this hook reindexes just
that file if it lives under a Somnium-managed directory. Files outside
managed dirs are ignored silently.

The hook is designed to be fast and fail-safe: any exception is logged
to `~/.claude/somnium/logs/hooks.log` and the hook exits 0 so it never
blocks Claude Code.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from ..code.chunker import DEFAULT_CODE_EXTENSIONS
from ..code.indexer import index_single_code_file
from ..code.walker import ALWAYS_IGNORE_DIRS
from ..config import load_config
from ..indexer import index_single_file
from ..storage.vector import VectorStore
from ._common import PathRoute, classify_path, log_error, log_info, read_event

HOOK_NAME = "post_tool_use"
RELEVANT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _extract_file_paths(event: dict[str, Any]) -> list[str]:
    """Pull the file_path(s) out of a PostToolUse event.

    Different tools have slightly different input shapes, so we look
    in a few known places.
    """
    tool_input = event.get("tool_input") or {}
    paths: list[str] = []

    # Write / Edit
    fp = tool_input.get("file_path")
    if isinstance(fp, str):
        paths.append(fp)

    # NotebookEdit
    nb_path = tool_input.get("notebook_path")
    if isinstance(nb_path, str):
        paths.append(nb_path)

    # MultiEdit can have a single file_path; if in the future a variant
    # carries multiple paths we handle that too.
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict):
                inner = edit.get("file_path")
                if isinstance(inner, str):
                    paths.append(inner)

    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _reindex_one(path: Path, route: PathRoute, config) -> int:
    """Reindex a single file. Returns the number of chunks written."""
    with VectorStore(route.store_path) as store:
        stats = index_single_file(
            store=store, path=path, kind=route.kind, config=config
        )
        return stats.chunks_upserted


def handle_event(event: dict[str, Any]) -> dict[str, Any]:
    """Core hook logic. Returns a small structured result for logging."""
    tool_name = event.get("tool_name")
    if tool_name not in RELEVANT_TOOLS:
        return {"skipped": "not a relevant tool", "tool": tool_name}

    cwd = event.get("cwd")
    start: Path | None = Path(cwd) if cwd else None
    config = load_config(project_root=None)  # re-resolve with cwd-aware search
    # If the hook's cwd differs from our process cwd, rediscover the
    # project root from there.
    if start is not None:
        from ..config import find_project_root

        new_root = find_project_root(start)
        if new_root is not None and new_root != config.project_root:
            config = load_config(project_root=new_root)

    file_paths = _extract_file_paths(event)
    if not file_paths:
        return {"skipped": "no file_path in tool_input"}

    results: list[dict[str, Any]] = []
    for fp in file_paths:
        path = Path(fp)

        # 1. Memory / skills scope (markdown)
        if fp.endswith(".md"):
            route = classify_path(path, config)
            if route is not None:
                chunks = _reindex_one(path, route, config)
                results.append(
                    {
                        "path": fp,
                        "scope": route.scope,
                        "chunks": chunks,
                    }
                )
                continue
            # fall through: might still be a code-indexed .md (e.g. docs)

        # 2. Project code scope (any source file under the project root)
        code_update = _try_code_reindex(path, config)
        if code_update is not None:
            results.append(code_update)
            continue

        # 3. Nothing matched
        results.append({"path": fp, "skipped": "out of scope"})

    return {"reindexed": results}


def _try_code_reindex(path: Path, config) -> dict[str, Any] | None:
    """If `path` is a source file under the current project and the
    project has a code index, reindex it. Returns a record dict or None
    if this file is not eligible for code indexing.
    """
    if config.project_root is None or config.project_code_index_path is None:
        return None
    if not config.project_code_index_path.exists():
        return None

    try:
        path.resolve().relative_to(config.project_root.resolve())
    except (ValueError, FileNotFoundError):
        return None

    # Skip ignored directories.
    for part in path.parts:
        if part in ALWAYS_IGNORE_DIRS or part in config.code_search.ignore:
            return None

    if path.suffix.lower() not in DEFAULT_CODE_EXTENSIONS:
        return None

    with VectorStore(config.project_code_index_path) as store:
        stats = index_single_code_file(store=store, path=path, config=config)

    return {
        "path": str(path),
        "scope": "code",
        "chunks": stats.chunks_upserted,
    }


def main() -> None:
    event = read_event()
    try:
        result = handle_event(event)
        # Log only if something actually happened.
        if result.get("reindexed"):
            log_info(HOOK_NAME, str(result))
    except BaseException as exc:  # noqa: BLE001
        log_error(HOOK_NAME, exc)
    # Always exit 0 so Claude Code is never blocked by us.
    sys.exit(0)


if __name__ == "__main__":
    main()
