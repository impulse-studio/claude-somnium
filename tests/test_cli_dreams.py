"""Tests for the ``somnium dreams`` CLI command."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

runner = CliRunner()


def _write_digest(sessions_dir: Path, filename: str, meta: dict, body: str = "") -> None:
    """Write a fake digest markdown with YAML frontmatter."""
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    if body:
        lines.append(body)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / filename).write_text("\n".join(lines), encoding="utf-8")


def _make_app(tmp_path: Path, monkeypatch):
    """Patch get_config to use tmp_path, return the Typer app."""
    from somnium.config import SomniumConfig

    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "somnium")
    (tmp_path / "somnium").mkdir(parents=True, exist_ok=True)

    from somnium.cli import dreams as dreams_mod  # noqa: F811

    monkeypatch.setattr(dreams_mod, "get_config", lambda: cfg)
    from somnium.cli import app

    return app, cfg


def test_dreams_lists_recent_digests(tmp_path, monkeypatch):
    app, cfg = _make_app(tmp_path, monkeypatch)
    sessions = cfg.dream_dir / "sessions"
    _write_digest(sessions, "2026-04-10T120000-abc12345.md", {
        "session_id": "abc12345-full-id",
        "timestamp": "2026-04-10T12:00:00+00:00",
        "cwd": "/home/user/project",
        "gate_decision": "run",
        "category": "project_memory",
        "user_messages": 5,
        "file_writes": 3,
    })
    _write_digest(sessions, "2026-04-11T080000-def67890.md", {
        "session_id": "def67890-full-id",
        "timestamp": "2026-04-11T08:00:00+00:00",
        "cwd": "/home/user/other",
        "gate_decision": "skip",
        "user_messages": 2,
        "file_writes": 0,
    })
    _write_digest(sessions, "2026-04-12T100000-ghi11111.md", {
        "session_id": "ghi11111-full-id",
        "timestamp": "2026-04-12T10:00:00+00:00",
        "cwd": "/tmp/test",
        "gate_decision": "run",
        "category": "global_memory",
        "user_messages": 10,
        "file_writes": 2,
    })

    result = runner.invoke(app, ["dreams"])
    assert result.exit_code == 0
    assert "abc12345" in result.output
    assert "def67890" in result.output
    assert "ghi11111" in result.output


def test_dreams_last_limits_output(tmp_path, monkeypatch):
    app, cfg = _make_app(tmp_path, monkeypatch)
    sessions = cfg.dream_dir / "sessions"
    for i in range(5):
        _write_digest(sessions, f"2026-04-{10 + i:02d}T120000-sid{i}abcd.md", {
            "session_id": f"sid{i}abcd-full-id-here",
            "timestamp": f"2026-04-{10 + i:02d}T12:00:00+00:00",
            "cwd": "/tmp",
            "gate_decision": "run",
            "user_messages": 1,
            "file_writes": 0,
        })

    result = runner.invoke(app, ["dreams", "--last", "2"])
    assert result.exit_code == 0
    # Most recent 2 (sorted desc by filename); session[:8] = "sid4abcd", "sid3abcd"
    assert "sid4abcd" in result.output
    assert "sid3abcd" in result.output
    # Older ones should not appear
    assert "sid0abcd" not in result.output


def test_dreams_empty_dir(tmp_path, monkeypatch):
    app, cfg = _make_app(tmp_path, monkeypatch)
    result = runner.invoke(app, ["dreams"])
    assert result.exit_code == 0
    assert "No dream sessions found" in result.output


def test_dreams_json_output(tmp_path, monkeypatch):
    app, cfg = _make_app(tmp_path, monkeypatch)
    sessions = cfg.dream_dir / "sessions"
    _write_digest(sessions, "2026-04-10T120000-aaa00000.md", {
        "session_id": "aaa00000-full",
        "timestamp": "2026-04-10T12:00:00+00:00",
        "cwd": "/tmp",
        "gate_decision": "run",
        "category": "project_skill",
        "user_messages": 3,
        "file_writes": 1,
    })

    result = runner.invoke(app, ["dreams", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["session_id"] == "aaa00000-full"
    assert parsed[0]["gate_decision"] == "run"


def test_dreams_json_empty(tmp_path, monkeypatch):
    app, cfg = _make_app(tmp_path, monkeypatch)
    result = runner.invoke(app, ["dreams", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed == []
