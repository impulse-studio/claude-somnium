"""Tests for the ``somnium dream list`` and ``somnium dream run`` CLI commands."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from somnium.cli.dream import _find_latest_transcript, _short_cwd

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

    from somnium.cli import dream as dream_mod

    monkeypatch.setattr(dream_mod, "get_config", lambda: cfg)
    from somnium.cli import app

    return app, cfg


def test_dream_list_shows_recent_digests(tmp_path, monkeypatch):
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

    result = runner.invoke(app, ["dream", "list"])
    assert result.exit_code == 0
    assert "abc12345" in result.output
    assert "def67890" in result.output
    assert "ghi11111" in result.output


def test_dream_list_last_limits_output(tmp_path, monkeypatch):
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

    result = runner.invoke(app, ["dream", "list", "--last", "2"])
    assert result.exit_code == 0
    # Most recent 2 (sorted desc by filename); session[:8] = "sid4abcd", "sid3abcd"
    assert "sid4abcd" in result.output
    assert "sid3abcd" in result.output
    # Older ones should not appear
    assert "sid0abcd" not in result.output


def test_dream_list_empty_dir(tmp_path, monkeypatch):
    app, cfg = _make_app(tmp_path, monkeypatch)
    result = runner.invoke(app, ["dream", "list"])
    assert result.exit_code == 0
    assert "No dream sessions found" in result.output


def test_dream_list_json_output(tmp_path, monkeypatch):
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

    result = runner.invoke(app, ["dream", "list", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["session_id"] == "aaa00000-full"
    assert parsed[0]["gate_decision"] == "run"


def test_dream_list_json_empty(tmp_path, monkeypatch):
    app, cfg = _make_app(tmp_path, monkeypatch)
    result = runner.invoke(app, ["dream", "list", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed == []


def test_dream_list_all_flag(tmp_path, monkeypatch):
    """--all shows all digests regardless of --last."""
    app, cfg = _make_app(tmp_path, monkeypatch)
    sessions = cfg.dream_dir / "sessions"
    for i in range(15):
        _write_digest(sessions, f"2026-04-{i + 1:02d}T120000-sid{i:05d}.md", {
            "session_id": f"sid{i:05d}-full",
            "timestamp": f"2026-04-{i + 1:02d}T12:00:00+00:00",
            "cwd": "/tmp",
            "gate_decision": "run",
            "user_messages": 1,
            "file_writes": 0,
        })

    result = runner.invoke(app, ["dream", "list", "--all"])
    assert result.exit_code == 0
    assert "15 shown" in result.output


# ---------------------------------------------------------------------------
# _short_cwd
# ---------------------------------------------------------------------------


class TestShortCwd:
    def test_empty(self):
        assert _short_cwd("") == "-"

    def test_dash(self):
        assert _short_cwd("-") == "-"

    def test_short_path(self):
        assert _short_cwd("/tmp/foo") == "/tmp/foo"

    def test_long_path_truncated(self):
        long = "/home/user/very/long/path/to/project/root"
        result = _short_cwd(long, max_len=20)
        assert result.startswith("...")
        assert len(result) <= 20


# ---------------------------------------------------------------------------
# _find_latest_transcript
# ---------------------------------------------------------------------------


def test_find_latest_transcript_no_projects(tmp_path, monkeypatch):
    """Returns None when ~/.claude/projects doesn't exist."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = _find_latest_transcript()
    assert result is None


# ---------------------------------------------------------------------------
# somnium dream run — error cases
# ---------------------------------------------------------------------------


def test_dream_run_no_api_key(tmp_path, monkeypatch):
    """dream run exits 1 with no Voyage API key."""
    app, cfg = _make_app(tmp_path, monkeypatch)
    cfg.embeddings.api_key = None
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    from somnium.cli import dream as dream_mod

    monkeypatch.setattr(dream_mod, "reset_config_cache", lambda: None)

    result = runner.invoke(app, ["dream", "run"])
    assert result.exit_code == 1
    assert "Voyage API key" in result.output


def test_dream_run_no_transcript(tmp_path, monkeypatch):
    """dream run exits 1 when no transcript found."""
    app, cfg = _make_app(tmp_path, monkeypatch)
    cfg.embeddings.api_key = "fake"

    from somnium.cli import dream as dream_mod

    monkeypatch.setattr(dream_mod, "reset_config_cache", lambda: None)
    monkeypatch.setattr(dream_mod, "_find_latest_transcript", lambda: None)

    result = runner.invoke(app, ["dream", "run"])
    assert result.exit_code == 1
    assert "no transcript" in result.output


# ---------------------------------------------------------------------------
# _print_dream_result
# ---------------------------------------------------------------------------


def test_print_dream_result_skip(tmp_path):
    """Print result for a skip gate decision."""
    from somnium.cli.dream import _print_dream_result
    from somnium.dream.gate import GateDecision

    class FakeGate:
        decision = GateDecision.SKIP
        reason = "too short"

    class FakeResult:
        gate_result = FakeGate()
        dream_result = None
        write_records = None
        error = None
        digest_path = None

    _print_dream_result(FakeResult())


def test_print_dream_result_run_with_records(tmp_path):
    """Print result for a run gate with write records."""
    from somnium.cli.dream import _print_dream_result
    from somnium.dream.gate import GateDecision

    class FakeGate:
        decision = GateDecision.RUN
        reason = "meaningful session"

    class FakeDreamResult:
        should_persist = True
        items: list = [{"title": "Test"}]  # noqa: RUF012
        summary = "A summary"

    class FakeRecord:
        status = "written"
        category = "global_memory"
        title = "Test Memory"
        path = "/home/memory/test.md"
        reason = None

    class FakeResult:
        gate_result = FakeGate()
        dream_result = FakeDreamResult()
        write_records: list = [FakeRecord()]  # noqa: RUF012
        error = None
        digest_path = "/home/dream/sessions/digest.md"

    _print_dream_result(FakeResult())


def test_print_dream_result_with_error():
    """Print result with an error."""
    from somnium.cli.dream import _print_dream_result
    from somnium.dream.gate import GateDecision

    class FakeGate:
        decision = GateDecision.RUN
        reason = "ok"

    class FakeResult:
        gate_result = FakeGate()
        dream_result = None
        write_records = None
        error = "something went wrong"
        digest_path = None

    _print_dream_result(FakeResult())
