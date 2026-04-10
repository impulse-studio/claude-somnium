"""Write per-session dream digests to disk.

Each dream run produces a markdown file at
  ~/.claude/somnium/dream/sessions/<date>-<session-id>.md

The digest records: gate decision, session stats, dream agent summary,
items that were written (with paths), items that were skipped, and the
raw agent stdout for debugging.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ..config import SomniumConfig
from .agent import DreamResult
from .gate import GateResult
from .router import WriteRecord
from .transcript import Transcript


def _fmt_path(p: str) -> str:
    if not p:
        return "-"
    return f"`{p}`"


def write_digest(
    *,
    config: SomniumConfig,
    transcript: Transcript,
    gate: GateResult,
    dream: DreamResult | None,
    records: list[WriteRecord] | None,
    error: str | None = None,
) -> Path:
    """Render a digest markdown and write it under dream/sessions/."""
    sessions_dir = config.dream_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    now = dt.datetime.now()
    session_id = transcript.session_id or "unknown"
    filename = f"{now.strftime('%Y-%m-%dT%H%M%S')}-{session_id[:8]}.md"
    target = sessions_dir / filename

    lines: list[str] = []
    lines.append("---")
    lines.append(f"session_id: {session_id}")
    lines.append(f"timestamp: {now.isoformat()}")
    lines.append(f"cwd: {transcript.cwd or '-'}")
    lines.append(f"gate_decision: {gate.decision.value}")
    if gate.category:
        lines.append(f"category: {gate.category}")
    lines.append(f"user_messages: {transcript.n_user_messages}")
    lines.append(f"file_writes: {len(transcript.file_writes)}")
    lines.append("---\n")

    lines.append(f"# Dream digest — {now.strftime('%Y-%m-%d %H:%M')}\n")

    lines.append("## Gate")
    lines.append(f"- Decision: **{gate.decision.value}**")
    lines.append(f"- Reason: {gate.reason}")
    if gate.category:
        lines.append(f"- Category: {gate.category}")
    lines.append("")

    if gate.decision.value == "skip":
        lines.append("_Dream agent was not run._\n")

    if dream is not None:
        lines.append("## Dream agent summary")
        lines.append(dream.summary or "_(no summary)_")
        lines.append("")
        lines.append(f"- should_persist: `{dream.should_persist}`")
        lines.append(f"- items returned: {len(dream.items)}")
        lines.append("")

    if records:
        lines.append("## Items written")
        lines.append("")
        lines.append("| Status | Category | Title | Path | Reason |")
        lines.append("|---|---|---|---|---|")
        for r in records:
            lines.append(
                f"| {r.status} | {r.category} | {r.title} | "
                f"{_fmt_path(r.path)} | {r.reason or ''} |"
            )
        lines.append("")

    if error:
        lines.append("## Error")
        lines.append("```")
        lines.append(error)
        lines.append("```")
        lines.append("")

    if dream is not None and dream.raw_stdout:
        lines.append("<details><summary>Raw agent output</summary>\n")
        lines.append("```json")
        lines.append(dream.raw_stdout[:8000])
        lines.append("```\n</details>")

    target.write_text("\n".join(lines), encoding="utf-8")
    return target
