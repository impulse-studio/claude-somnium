"""Tests for dream transcript parsing."""

from __future__ import annotations

import json
from pathlib import Path

from somnium.dream.transcript import _is_tool_result_only, load_transcript


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )


def test_empty_file_returns_empty_transcript(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    p.write_text("", encoding="utf-8")
    tr = load_transcript(p)
    assert tr.turns == []
    assert tr.n_user_messages == 0


def test_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "nope.jsonl"
    tr = load_transcript(p)
    assert tr.turns == []


def test_basic_user_and_assistant(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            {"type": "permission-mode", "sessionId": "abc"},
            {
                "type": "user",
                "sessionId": "abc",
                "cwd": "/work",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "hello"}],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "hi there"},
                        {
                            "type": "tool_use",
                            "name": "Write",
                            "input": {"file_path": "/work/a.md", "content": "x"},
                        },
                    ],
                },
            },
        ],
    )
    tr = load_transcript(p)
    assert tr.session_id == "abc"
    assert tr.cwd == "/work"
    assert len(tr.turns) == 2
    assert tr.turns[0].text == "hello"
    assert tr.turns[1].tool_uses[0]["name"] == "Write"
    assert tr.file_writes == ["/work/a.md"]
    assert tr.n_user_messages == 1


def test_skips_local_command_caveats(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            {
                "type": "user",
                "isMeta": True,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "<local-command-caveat>this is internal",
                        }
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "real message"}],
                },
            },
        ],
    )
    tr = load_transcript(p)
    # Only the real user message should count
    assert tr.n_user_messages == 1
    assert tr.user_turns[0].text == "real message"


def test_as_markdown_renders_turns(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            {
                "type": "user",
                "message": {"role": "user", "content": "hello"},
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "hi"},
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        },
                    ],
                },
            },
        ],
    )
    tr = load_transcript(p)
    md = tr.as_markdown()
    assert "## USER" in md
    assert "## ASSISTANT" in md
    assert "hello" in md
    assert "Bash" in md


def test_tool_uses_on_multiedit(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "MultiEdit",
                            "input": {
                                "file_path": "/work/foo.md",
                                "edits": [],
                            },
                        }
                    ],
                },
            }
        ],
    )
    tr = load_transcript(p)
    assert tr.file_writes == ["/work/foo.md"]


# ---------------------------------------------------------------------------
# tool_result filtering — n_user_messages should count real prompts only
# ---------------------------------------------------------------------------


def test_is_tool_result_only_true():
    assert _is_tool_result_only("[tool_result]") is True
    assert _is_tool_result_only("[tool_result]\n[tool_result]") is True
    assert _is_tool_result_only("  [tool_result]  ") is True


def test_is_tool_result_only_false():
    assert _is_tool_result_only("actual human text") is False
    assert _is_tool_result_only("[tool_result] and some real text") is False
    assert _is_tool_result_only("") is False


def test_tool_result_turns_excluded_from_n_user_messages(tmp_path: Path) -> None:
    """A single-shot `claude -p` with tool calls should count as 1
    user message, not 1 + N tool_result turns."""
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            # Real user prompt
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "add type hints to all functions"}],
                },
            },
            # Assistant calls a tool
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Edit", "input": {"file_path": "/x.py"}},
                    ],
                },
            },
            # Tool result comes back as a "user" message
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "content": "ok"}],
                },
            },
            # Another tool result
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "content": "done"}],
                },
            },
            # Final assistant reply
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Done, added type hints."}],
                },
            },
        ],
    )
    tr = load_transcript(p)

    # user_turns includes everything (real + tool results)
    assert len(tr.user_turns) == 3

    # real_user_turns excludes tool-result-only turns
    assert len(tr.real_user_turns) == 1
    assert tr.real_user_turns[0].text == "add type hints to all functions"

    # n_user_messages uses real_user_turns
    assert tr.n_user_messages == 1


def test_mixed_content_turn_is_not_filtered(tmp_path: Path) -> None:
    """A turn with BOTH real text and tool_result markers should count
    as a real user message."""
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "content": "ok"},
                        {"type": "text", "text": "now do the next step"},
                    ],
                },
            },
        ],
    )
    tr = load_transcript(p)
    assert tr.n_user_messages == 1
    assert "next step" in tr.real_user_turns[0].text
