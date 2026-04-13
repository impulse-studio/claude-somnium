"""UserPromptSubmit hook: inject top-K memory results as context.

Claude Code fires this hook right after the user submits a prompt.
The hook:
  1. Reads the event JSON from stdin.
  2. Embeds the prompt via Voyage.
  3. Searches global and project memory for the top-K most relevant
     chunks (within a configured token budget).
  4. Emits structured output so Claude Code injects the results as
     additional context for this turn.

Config (in ~/.claude/somnium/config.toml):

    [context_injection]
    enabled = true
    top_k = 5
    context_budget_tokens = 2000
    scopes = ["project", "global", "skills"]

The injected context is plain markdown, clearly labeled so the model
knows it came from Somnium.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import SomniumConfig
    from ..storage.vector import SearchHit

from ..config import find_project_root, load_config
from ..storage.parquet_store import ParquetStore
from ..storage.scope import normalize_scopes
from ._common import log_error, log_info, read_event

HOOK_NAME = "user_prompt_submit"

# Rough char-per-token estimate. We truncate based on chars rather than
# pulling tiktoken to keep the hook dependency-free and fast.
CHARS_PER_TOKEN = 4
HEADER = "## Somnium: relevant memories\n"


def _extract_prompt(event: dict[str, Any]) -> str:
    """Pull the raw user prompt text out of the hook event."""
    # Different event shapes across Claude Code versions; check several.
    for key in ("prompt", "user_prompt", "userPrompt"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    # Newer versions nest it under hookInput or payload.
    nested = event.get("hookInput") or event.get("payload") or {}
    if isinstance(nested, dict):
        for key in ("prompt", "user_prompt", "text"):
            val = nested.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

    return ""


def _format_hits(
    hits: list[SearchHit], budget_tokens: int
) -> tuple[str, int]:
    """Render hits as a markdown block bounded by token budget.

    Returns (rendered_text, n_hits_included).
    """
    if not hits:
        return "", 0

    budget_chars = budget_tokens * CHARS_PER_TOKEN
    lines: list[str] = [HEADER]
    included = 0
    used = len(HEADER)

    for i, hit in enumerate(hits, 1):
        short_path = _short_path(hit.file_path)
        header = f"\n### {i}. {short_path}  \n_score={hit.score:.3f} scope={hit.scope}_\n\n"
        body = hit.text.strip()
        # Budget check
        if used + len(header) + len(body) > budget_chars and included > 0:
            break
        lines.append(header)
        lines.append(body)
        lines.append("")
        used += len(header) + len(body) + 1
        included += 1

    if included == 0:
        return "", 0
    return "\n".join(lines), included


def _short_path(path: str) -> str:
    """Collapse a full path to something compact for display."""
    p = Path(path)
    home = Path.home()
    try:
        rel = p.resolve().relative_to(home)
    except ValueError:
        return str(p)
    else:
        return f"~/{rel}"


def _search_all(prompt: str, config: SomniumConfig, top_k: int, scopes: list[str]) -> list[SearchHit]:
    """Run the search across global + project stores and merge."""
    from ..embeddings import get_embedder  # late import to keep hook startup fast

    embedder = get_embedder(config)
    query_vec = embedder.embed_query(prompt)

    reranker_enabled = (
        config.context_injection.reranker_enabled
        and config.embeddings.provider == "voyage"
    )
    retrieval_k = top_k * 3 if reranker_enabled else top_k

    hits: list[SearchHit] = []
    if config.global_index_path.exists():
        with ParquetStore(config.global_index_path) as store:
            hits.extend(store.search(query_vec, top_k=retrieval_k, scopes=scopes))
    if config.project_index_path and config.project_index_path.exists():
        with ParquetStore(config.project_index_path) as store:
            hits.extend(store.search(query_vec, top_k=retrieval_k, scopes=scopes))

    hits.sort(key=lambda h: h.score, reverse=True)
    hits = hits[:retrieval_k]

    if reranker_enabled and hits:
        hits = _rerank_hits(prompt, hits, embedder, config)

    return hits[:top_k]


def _rerank_hits(
    query: str,
    hits: list[SearchHit],
    embedder: object,
    config: SomniumConfig,
) -> list[SearchHit]:
    """Re-score hits using the Voyage reranker and return reordered list."""
    from ..storage.vector import SearchHit as _SearchHit

    documents = [h.text for h in hits]
    model = config.context_injection.reranker_model
    results = embedder.rerank(query, documents, model=model)  # type: ignore[union-attr]

    reranked: list[_SearchHit] = []
    for r in results:
        hit = hits[r.index]
        reranked.append(
            _SearchHit(
                file_path=hit.file_path,
                chunk_idx=hit.chunk_idx,
                scope=hit.scope,
                score=r.score,
                text=hit.text,
                heading_path=hit.heading_path,
                tags=hit.tags,
            )
        )
    return reranked


def _hit_title(hit: SearchHit) -> str:
    """Extract a short human title from a search hit."""
    if hit.heading_path:
        return hit.heading_path[-1]
    return Path(hit.file_path).stem


def handle_event(event: dict[str, Any]) -> dict[str, Any]:
    """Core logic. Returns a dict that maps to a hookSpecificOutput payload."""
    prompt = _extract_prompt(event)
    if not prompt:
        return {"skipped": "empty prompt"}

    session_id = event.get("session_id") or ""

    cwd = event.get("cwd")
    project_root = find_project_root(Path(cwd)) if cwd else None
    config = load_config(project_root=project_root)

    # Set cost tracking project context
    from ..cost import set_project

    set_project(project_root.name if project_root else "global")

    if not config.context_injection.enabled:
        return {"skipped": "context_injection disabled", "session_id": session_id}

    # Don't call Voyage if no API key is configured — just skip.
    if config.embeddings.resolve_api_key() is None:
        return {"skipped": "no Voyage API key", "session_id": session_id}

    scopes = normalize_scopes(config.context_injection.scopes)
    hits = _search_all(
        prompt=prompt,
        config=config,
        top_k=config.context_injection.top_k,
        scopes=scopes,
    )

    text, n_included = _format_hits(
        hits, budget_tokens=config.context_injection.context_budget_tokens
    )
    if n_included == 0:
        return {"skipped": "no hits", "session_id": session_id}

    # Count skills vs memories from the included hits (first n_included).
    included_hits = hits[:n_included]
    n_skills = sum(1 for h in included_hits if "skill" in h.scope)
    n_memories = n_included - n_skills

    return {
        "injected": True,
        "session_id": session_id,
        "n_hits": n_included,
        "n_skills": n_skills,
        "n_memories": n_memories,
        "text": text,
        "included_hits": included_hits,
    }


def main() -> None:
    event = read_event()
    try:
        result = handle_event(event)
    except BaseException as exc:
        log_error(HOOK_NAME, exc)
        sys.exit(0)

    session_id = result.get("session_id", "")

    # Emit structured output only if we have something to inject.
    if result.get("injected"):
        payload = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": result["text"],
            }
        }
        sys.stdout.write(json.dumps(payload))
        sys.stdout.flush()
        log_info(
            HOOK_NAME,
            f"injected {result['n_hits']} hits "
            f"({result.get('n_skills', 0)} skills, "
            f"{result.get('n_memories', 0)} mem), "
            f"{len(result['text'])} chars",
        )
        included_hits = result.get("included_hits") or []
        _write_state(
            session_id=session_id,
            hits=[
                {
                    "title": _hit_title(h),
                    "scope": h.scope,
                    "score": round(h.score, 3),
                    "path": _short_path(h.file_path),
                }
                for h in included_hits
            ],
        )
    else:
        log_info(HOOK_NAME, str(result))
        # Don't write zeros — preserve existing cumulative state.

    sys.exit(0)


STATE_DIR = Path.home() / ".claude" / "somnium" / "state"
_STATE_MAX_AGE_SECONDS = 86400  # 24h — cleanup stale session files


def _state_filename(session_id: str) -> str:
    """Return the state filename for a session (or the legacy name)."""
    if session_id:
        return f"prompt_context_{session_id}.json"
    return "prompt_context.json"


def _write_state(
    *,
    session_id: str,
    hits: list[dict[str, object]] | None = None,
) -> None:
    """Append new hits to the per-session state file, deduplicating by
    (title, scope). Counts are recomputed from the merged list so the
    status line and /somnium:context always show cumulative data.
    Best-effort, non-fatal."""
    try:
        import datetime as dt

        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state_path = STATE_DIR / _state_filename(session_id)

        # Read existing cumulative hits.
        existing_hits: list[dict[str, object]] = []
        if state_path.exists():
            try:
                existing = json.loads(state_path.read_text(encoding="utf-8"))
                existing_hits = existing.get("hits", [])
            except Exception:  # noqa: S110
                pass

        # Merge: deduplicate by (title, scope), keep higher score.
        seen: dict[tuple[str, str], int] = {}
        for i, h in enumerate(existing_hits):
            seen[(str(h.get("title", "")), str(h.get("scope", "")))] = i

        merged = list(existing_hits)
        for h in hits or []:
            key = (str(h.get("title", "")), str(h.get("scope", "")))
            if key in seen:
                idx = seen[key]
                if float(h.get("score", 0)) > float(merged[idx].get("score", 0)):
                    merged[idx]["score"] = h["score"]
            else:
                seen[key] = len(merged)
                merged.append(h)

        # Recount from merged list.
        n_skills = sum(1 for h in merged if "skill" in str(h.get("scope", "")))
        n_memories = len(merged) - n_skills

        state_path.write_text(
            json.dumps(
                {
                    "session_id": session_id,
                    "n_hits": len(merged),
                    "n_skills": n_skills,
                    "n_memories": n_memories,
                    "timestamp": dt.datetime.now(tz=dt.UTC).isoformat(),
                    "hits": merged,
                }
            ),
            encoding="utf-8",
        )
        _cleanup_old_state_files()
    except Exception:  # noqa: S110
        pass


def _cleanup_old_state_files() -> None:
    """Remove state files older than 24h to prevent buildup."""
    import time

    try:
        now = time.time()
        for path in STATE_DIR.glob("prompt_context_*.json"):
            if now - path.stat().st_mtime > _STATE_MAX_AGE_SECONDS:
                path.unlink(missing_ok=True)
    except Exception:  # noqa: S110
        pass


if __name__ == "__main__":
    main()
