"""Parse Claude Code session transcripts.

Transcripts are JSONL files in ~/.claude/projects/<encoded-cwd>/<session>.jsonl.
Each line is an event. We only care about `user` and `assistant` events
and extract their plain-text / tool-use content in chronological order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class Turn:
    """One message in the conversation, simplified."""

    role: str  # "user" or "assistant"
    text: str  # concatenated text content (no tool payloads)
    tool_uses: list[dict[str, Any]] = field(default_factory=list)  # name, input summary
    timestamp: str | None = None


@dataclass
class Transcript:
    session_id: str | None
    cwd: str | None
    turns: list[Turn]

    @property
    def user_turns(self) -> list[Turn]:
        """All user turns including tool-result-only ones."""
        return [t for t in self.turns if t.role == "user" and t.text.strip()]

    @property
    def real_user_turns(self) -> list[Turn]:
        """User turns that contain actual human text, not just
        [tool_result] markers injected by Claude Code.

        A single `claude -p` call can generate many user-role turns
        because tool results are sent back as user messages in the
        Anthropic API. Without this filter, a 1-prompt session with
        6 tool calls looks like "7 user messages" to the gate.
        """
        return [
            t
            for t in self.turns
            if t.role == "user"
            and t.text.strip()
            and not _is_tool_result_only(t.text)
        ]

    @property
    def assistant_turns(self) -> list[Turn]:
        return [t for t in self.turns if t.role == "assistant"]

    @property
    def n_user_messages(self) -> int:
        """Count of real human user messages (excludes tool_result turns)."""
        return len(self.real_user_turns)

    @property
    def file_writes(self) -> list[str]:
        """File paths written or edited during the session."""
        paths: list[str] = []
        for t in self.assistant_turns:
            for tu in t.tool_uses:
                if tu.get("name") in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
                    inp = tu.get("input") or {}
                    p = inp.get("file_path") or inp.get("notebook_path")
                    if isinstance(p, str):
                        paths.append(p)
        return paths

    def as_markdown(self, max_chars_per_turn: int = 4000) -> str:
        """Render the conversation as a compact markdown string suitable
        for feeding to the dream agent."""
        lines: list[str] = []
        for t in self.turns:
            if not t.text.strip() and not t.tool_uses:
                continue
            if t.role == "user":
                lines.append("## USER\n")
            else:
                lines.append("## ASSISTANT\n")
            if t.text.strip():
                body = t.text.strip()
                if len(body) > max_chars_per_turn:
                    body = body[:max_chars_per_turn] + "\n[…truncated…]"
                lines.append(body)
            if t.tool_uses:
                tool_summary = ", ".join(
                    f"{tu.get('name', '?')}({_summarize_input(tu.get('input'))})"
                    for tu in t.tool_uses
                )
                lines.append(f"\n_tools: {tool_summary}_")
            lines.append("")
        return "\n".join(lines)


def _summarize_input(inp: Any, max_len: int = 80) -> str:
    if not isinstance(inp, dict):
        return ""
    # Prefer file_path or command if present, otherwise first value.
    for key in ("file_path", "notebook_path", "command", "pattern", "url"):
        if key in inp and isinstance(inp[key], str):
            val = inp[key]
            return f"{key}={val[:max_len]}"
    keys = list(inp.keys())[:2]
    return ",".join(keys)


_TOOL_RESULT_MARKER = "[tool_result]"


def _is_tool_result_only(text: str) -> bool:
    """True if the text consists solely of [tool_result] markers and whitespace."""
    stripped = text.strip()
    if not stripped:
        return False
    # Remove all markers, see if anything real is left.
    cleaned = stripped.replace(_TOOL_RESULT_MARKER, "").strip()
    return len(cleaned) == 0


def _extract_text_and_tools(content: Any) -> tuple[str, list[dict[str, Any]]]:
    """Pull plain text and tool_use blocks out of a message content payload."""
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        return "", []

    text_parts: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            t = block.get("text")
            if isinstance(t, str):
                text_parts.append(t)
        elif btype == "tool_use":
            tool_uses.append(
                {
                    "name": block.get("name"),
                    "input": block.get("input"),
                }
            )
        elif btype == "tool_result":
            # Keep a marker so we can identify tool-result-only turns
            # in _is_tool_result_only(). The marker is stripped by the
            # gate's real_user_turns filter.
            text_parts.append(_TOOL_RESULT_MARKER)
    return "\n".join(text_parts).strip(), tool_uses


def load_transcript(path: Path) -> Transcript:
    """Read a JSONL transcript file and return a structured Transcript."""
    turns: list[Turn] = []
    session_id: str | None = None
    cwd: str | None = None

    if not path.exists():
        return Transcript(session_id=None, cwd=None, turns=[])

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()  # noqa: PLW2901
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if session_id is None and isinstance(obj.get("sessionId"), str):
                session_id = obj["sessionId"]
            if cwd is None and isinstance(obj.get("cwd"), str):
                cwd = obj["cwd"]

            etype = obj.get("type")
            if etype not in {"user", "assistant"}:
                continue

            message = obj.get("message") or {}
            role = message.get("role") or etype
            content = message.get("content")
            text, tool_uses = _extract_text_and_tools(content)

            # Skip system-caveat meta messages — Claude Code injects these
            # for local commands and they are not real user input.
            if etype == "user" and (obj.get("isMeta") or "local-command-caveat" in text):
                continue

            turns.append(
                Turn(
                    role=role,
                    text=text,
                    tool_uses=tool_uses,
                    timestamp=obj.get("timestamp"),
                )
            )

    return Transcript(session_id=session_id, cwd=cwd, turns=turns)
