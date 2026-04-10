"""Tests for the Stop hook dispatcher.

The detached subprocess spawn (`subprocess.Popen`) is fully mocked, so
these tests never actually launch the dream runner. We verify the gate
flow, the recursion guard, and the dispatch decision.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from somnium.config import SomniumConfig
from somnium.dream.agent import DREAM_SUBAGENT_ENV_VAR
from somnium.hooks import stop as stop_hook


def _write_transcript(path: Path, n_user: int = 4, with_writes: bool = True) -> Path:
    """Build a JSONL transcript that passes the gate."""
    events: list[dict] = [{"type": "permission-mode", "sessionId": "stop-test"}]
    for i in range(n_user):
        events.append(
            {
                "type": "user",
                "sessionId": "stop-test",
                "cwd": "/tmp",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": f"meaningful prompt {i}"}],
                },
            }
        )
        events.append(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": (
                        [
                            {"type": "text", "text": "ok"},
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {"file_path": "/tmp/x.py"},
                            },
                        ]
                        if with_writes and i == 0
                        else [{"type": "text", "text": "ok"}]
                    ),
                },
            }
        )
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def sandbox_cfg(tmp_path, monkeypatch):
    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "home")
    (tmp_path / "home" / "dream" / "sessions").mkdir(parents=True)

    monkeypatch.setattr(stop_hook, "load_config", lambda project_root=None: cfg)
    return cfg


# ---------------------------------------------------------------------------
# Recursion guard
# ---------------------------------------------------------------------------


def test_recursion_guard_skips_when_env_var_set(sandbox_cfg, tmp_path, monkeypatch):
    """If SOMNIUM_DREAM_SUBAGENT=1 is in the env, the hook must no-op
    immediately. This is what prevents the dream sub-agent from
    triggering another dream when its own session ends."""
    transcript = _write_transcript(tmp_path / "t.jsonl")
    monkeypatch.setenv(DREAM_SUBAGENT_ENV_VAR, "1")

    result = stop_hook.handle_event(
        {"transcript_path": str(transcript), "cwd": "/tmp"}
    )
    assert "skipped" in result
    assert "sub-agent" in result["skipped"]


# ---------------------------------------------------------------------------
# Missing inputs
# ---------------------------------------------------------------------------


def test_no_transcript_path_is_skipped(sandbox_cfg, monkeypatch):
    monkeypatch.delenv(DREAM_SUBAGENT_ENV_VAR, raising=False)
    result = stop_hook.handle_event({})
    assert result == {"skipped": "no transcript_path in event"}


def test_missing_transcript_file_is_skipped(sandbox_cfg, tmp_path, monkeypatch):
    monkeypatch.delenv(DREAM_SUBAGENT_ENV_VAR, raising=False)
    result = stop_hook.handle_event(
        {"transcript_path": str(tmp_path / "does-not-exist.jsonl")}
    )
    assert result == {"skipped": "transcript file does not exist"}


# ---------------------------------------------------------------------------
# Dream disabled
# ---------------------------------------------------------------------------


def test_dream_disabled_is_skipped(sandbox_cfg, tmp_path, monkeypatch):
    monkeypatch.delenv(DREAM_SUBAGENT_ENV_VAR, raising=False)
    sandbox_cfg.dream.enabled = False
    transcript = _write_transcript(tmp_path / "t.jsonl")

    result = stop_hook.handle_event(
        {"transcript_path": str(transcript), "cwd": "/tmp"}
    )
    assert result == {"skipped": "dream.enabled=false"}


# ---------------------------------------------------------------------------
# Gate skip — short session
# ---------------------------------------------------------------------------


def test_short_session_writes_skip_digest_no_spawn(sandbox_cfg, tmp_path, monkeypatch):
    monkeypatch.delenv(DREAM_SUBAGENT_ENV_VAR, raising=False)
    transcript = _write_transcript(tmp_path / "t.jsonl", n_user=1, with_writes=False)

    spawn_called = {"count": 0}

    def _fake_spawn(*args, **kwargs):
        spawn_called["count"] += 1

    monkeypatch.setattr(stop_hook, "_spawn_detached_runner", _fake_spawn)

    result = stop_hook.handle_event(
        {"transcript_path": str(transcript), "cwd": "/tmp"}
    )
    assert result["gate"] == "skip"
    assert spawn_called["count"] == 0
    # Skip digest still written
    digests = list((sandbox_cfg.dream_dir / "sessions").glob("*.md"))
    assert len(digests) == 1


# ---------------------------------------------------------------------------
# Gate run — meaningful session
# ---------------------------------------------------------------------------


def test_meaningful_session_dispatches_runner(sandbox_cfg, tmp_path, monkeypatch):
    monkeypatch.delenv(DREAM_SUBAGENT_ENV_VAR, raising=False)
    transcript = _write_transcript(tmp_path / "t.jsonl", n_user=4, with_writes=True)

    spawn_calls: list[tuple] = []

    def _fake_spawn(transcript_path, cwd):
        spawn_calls.append((transcript_path, cwd))

    monkeypatch.setattr(stop_hook, "_spawn_detached_runner", _fake_spawn)

    result = stop_hook.handle_event(
        {"transcript_path": str(transcript), "cwd": "/tmp"}
    )
    assert result["gate"] == "run"
    assert result["dispatched"] is True
    assert len(spawn_calls) == 1
    assert spawn_calls[0][0] == str(transcript)
    assert spawn_calls[0][1] == "/tmp"


# ---------------------------------------------------------------------------
# _resolve_dream_runner_path
# ---------------------------------------------------------------------------


def test_resolve_dream_runner_path_returns_string():
    """Smoke test: should return a string, not crash."""
    path = stop_hook._resolve_dream_runner_path()
    assert isinstance(path, str)
    assert "somnium-dream-run" in path


# ---------------------------------------------------------------------------
# main() entry — never raises
# ---------------------------------------------------------------------------


def test_main_exits_zero_on_invalid_event(monkeypatch, capsys):
    """Hooks must never propagate exceptions back to Claude Code."""
    monkeypatch.setattr("sys.stdin", _StringIO("not json at all"))
    monkeypatch.delenv(DREAM_SUBAGENT_ENV_VAR, raising=False)

    with pytest.raises(SystemExit) as exc_info:
        stop_hook.main()
    assert exc_info.value.code == 0


class _StringIO:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text
