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
from typing import Any

from ..config import find_project_root, load_config
from ..storage.scope import normalize_scopes
from ..storage.vector import SearchHit, VectorStore
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
        return f"~/{rel}"
    except ValueError:
        return str(p)


def _search_all(prompt: str, config, top_k: int, scopes: list[str]) -> list[SearchHit]:
    """Run the search across global + project stores and merge."""
    from ..embeddings import get_embedder  # late import to keep hook startup fast

    embedder = get_embedder(config)
    query_vec = embedder.embed_query(prompt)

    hits: list[SearchHit] = []
    if config.global_index_path.exists():
        with VectorStore(config.global_index_path) as store:
            hits.extend(store.search(query_vec, top_k=top_k, scopes=scopes))
    if config.project_index_path and config.project_index_path.exists():
        with VectorStore(config.project_index_path) as store:
            hits.extend(store.search(query_vec, top_k=top_k, scopes=scopes))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]


def handle_event(event: dict[str, Any]) -> dict[str, Any]:
    """Core logic. Returns a dict that maps to a hookSpecificOutput payload."""
    prompt = _extract_prompt(event)
    if not prompt:
        return {"skipped": "empty prompt"}

    cwd = event.get("cwd")
    project_root = find_project_root(Path(cwd)) if cwd else None
    config = load_config(project_root=project_root)

    if not config.context_injection.enabled:
        return {"skipped": "context_injection disabled"}

    # Don't call Voyage if no API key is configured — just skip.
    if config.embeddings.resolve_api_key() is None:
        return {"skipped": "no Voyage API key"}

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
        return {"skipped": "no hits"}

    # Count skills vs memories from the included hits (first n_included).
    included_hits = hits[:n_included]
    n_skills = sum(1 for h in included_hits if "skill" in h.scope)
    n_memories = n_included - n_skills

    return {
        "injected": True,
        "n_hits": n_included,
        "n_skills": n_skills,
        "n_memories": n_memories,
        "text": text,
    }


def main() -> None:
    event = read_event()
    try:
        result = handle_event(event)
    except BaseException as exc:  # noqa: BLE001
        log_error(HOOK_NAME, exc)
        sys.exit(0)

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
        _write_state(
            n_hits=result["n_hits"],
            n_skills=result.get("n_skills", 0),
            n_memories=result.get("n_memories", 0),
            chars=len(result["text"]),
        )
    else:
        log_info(HOOK_NAME, str(result))
        _write_state(n_hits=0, n_skills=0, n_memories=0, chars=0)

    sys.exit(0)


def _write_state(
    *, n_hits: int, n_skills: int, n_memories: int, chars: int
) -> None:
    """Write a small JSON file so the status line can show injection stats
    without parsing the hooks log. Best-effort, non-fatal."""
    try:
        import datetime as dt

        state_dir = Path.home() / ".claude" / "somnium" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_path = state_dir / "prompt_context.json"
        state_path.write_text(
            json.dumps(
                {
                    "n_hits": n_hits,
                    "n_skills": n_skills,
                    "n_memories": n_memories,
                    "chars": chars,
                    "timestamp": dt.datetime.now().isoformat(),
                }
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":
    main()
