"""Cost tracking for Somnium.

Logs every embedding call and dream agent run to a JSONL file at
``~/.claude/somnium/costs.jsonl``. Each line is a self-contained JSON
object with timestamp, source, project, model, tokens, and cost.

Sources:
  index  — Voyage API calls during indexing or search
  dream  — claude -p sub-agent runs

Pricing (as of 2026-04):
  voyage-3.5       $0.06 / 1M tokens
  voyage-3.5-lite  $0.02 / 1M tokens
  voyage-code-3    $0.18 / 1M tokens
  rerank-2-lite    $0.02 / 1M tokens
"""

from __future__ import annotations

import contextvars
import datetime as dt
import json
from pathlib import Path
from typing import Any

# Voyage pricing: $ per 1M tokens
VOYAGE_PRICING: dict[str, float] = {
    "voyage-3.5": 0.06,
    "voyage-3.5-lite": 0.02,
    "voyage-code-3": 0.18,
    "rerank-2-lite": 0.02,
}

# Default cost log location
_DEFAULT_LOG = Path.home() / ".claude" / "somnium" / "costs.jsonl"

# Context variable for the current project name.
# Set via set_project() before calling embed/index operations.
_current_project: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_project", default="global"
)


def set_project(name: str) -> None:
    """Set the current project name for cost logging."""
    _current_project.set(name)


def get_project() -> str:
    """Get the current project name."""
    return _current_project.get()


def _cost_path() -> Path:
    """Return the cost log path, respecting SOMNIUM_HOME."""
    import os

    home = os.environ.get("SOMNIUM_HOME")
    if home:
        return Path(home) / "costs.jsonl"
    return _DEFAULT_LOG


def log_cost(
    *,
    source: str,
    model: str,
    tokens: int,
    cost_usd: float,
    project: str | None = None,
    context: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """Append a cost entry to the JSONL log. Best-effort, never raises."""
    try:
        entry = {
            "ts": dt.datetime.now(tz=dt.UTC).isoformat(),
            "source": source,
            "project": project or get_project(),
            "model": model,
            "tokens": tokens,
            "cost_usd": round(cost_usd, 6),
            "context": context,
        }
        if extra:
            entry.update(extra)
        path = _cost_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: S110
        pass


def voyage_cost(model: str, total_tokens: int) -> float:
    """Compute the cost in USD for a Voyage API call."""
    rate = VOYAGE_PRICING.get(model, 0.06)
    return total_tokens * rate / 1_000_000


def read_costs(
    *,
    since: dt.datetime | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    """Read cost entries from the log, optionally filtered."""
    path = _cost_path()
    if not path.exists():
        return []

    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()  # noqa: PLW2901
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if source and entry.get("source") != source:
            continue
        if since:
            ts = entry.get("ts", "")
            try:
                entry_time = dt.datetime.fromisoformat(ts)
                if entry_time < since:
                    continue
            except (ValueError, TypeError):
                continue
        entries.append(entry)
    return entries


def summarize_costs(
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate cost entries into a summary."""
    total_usd = 0.0
    total_tokens = 0
    by_source: dict[str, float] = {}
    by_model: dict[str, float] = {}
    count = len(entries)

    for e in entries:
        cost = e.get("cost_usd", 0.0)
        tokens = e.get("tokens", 0)
        src = e.get("source", "unknown")
        model = e.get("model", "unknown")

        total_usd += cost
        total_tokens += tokens
        by_source[src] = by_source.get(src, 0.0) + cost
        by_model[model] = by_model.get(model, 0.0) + cost

    return {
        "total_usd": round(total_usd, 4),
        "total_tokens": total_tokens,
        "count": count,
        "by_source": {k: round(v, 4) for k, v in sorted(by_source.items())},
        "by_model": {k: round(v, 4) for k, v in sorted(by_model.items())},
    }
