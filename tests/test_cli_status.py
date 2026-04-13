"""Tests for ``somnium status`` command and helpers."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from somnium.cli import status as status_mod

runner = CliRunner()


def _patch_status(monkeypatch, tmp_path):
    """Minimal sandbox for status commands."""
    from somnium.config import SomniumConfig

    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "home")
    cfg.project_root = None

    monkeypatch.setattr(status_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(status_mod, "reset_config_cache", lambda: None)
    return cfg


# ---------------------------------------------------------------------------
# _index_row
# ---------------------------------------------------------------------------


class TestIndexRow:
    def test_none_path(self):
        row = status_mod._index_row("test", None)
        assert row[1] == "(not built)"

    def test_missing_path(self, tmp_path):
        row = status_mod._index_row("test", tmp_path / "nope.parquet")
        assert row[2] == "-"


# ---------------------------------------------------------------------------
# _read_somnium_hooks_from_settings
# ---------------------------------------------------------------------------


class TestReadHooks:
    def test_no_settings_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(status_mod, "Path", _FakePath(tmp_path / "nope"))
        # Direct call — just check it doesn't crash with real Path
        result = status_mod._read_somnium_hooks_from_settings()
        assert isinstance(result, dict)

    def test_parses_somnium_hooks(self, tmp_path, monkeypatch):
        settings = {
            "hooks": {
                "Stop": [
                    {
                        "_somnium": True,
                        "hooks": [{"command": "/usr/bin/somnium-stop"}],
                    }
                ],
                "PostToolUse": [
                    {
                        "_somnium": True,
                        "hooks": [{"command": "/usr/bin/somnium-post-tool-use"}],
                    }
                ],
            }
        }
        settings_path = Path.home() / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings), encoding="utf-8")

        result = status_mod._read_somnium_hooks_from_settings()
        assert "Stop" in result
        assert "PostToolUse" in result

    def test_skips_non_somnium_hooks(self, tmp_path, monkeypatch):
        settings = {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [{"command": "/usr/bin/other-tool"}],
                    }
                ],
            }
        }
        settings_path = Path.home() / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings), encoding="utf-8")

        result = status_mod._read_somnium_hooks_from_settings()
        assert "Stop" not in result

    def test_handles_malformed_hooks(self, tmp_path, monkeypatch):
        settings = {
            "hooks": {
                "Stop": "not a list",
                "Post": [42],
            }
        }
        settings_path = Path.home() / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(settings), encoding="utf-8")

        result = status_mod._read_somnium_hooks_from_settings()
        assert result == {}


# ---------------------------------------------------------------------------
# _check_mcp_server
# ---------------------------------------------------------------------------


class TestCheckMcp:
    def test_no_claude_cli(self, monkeypatch):
        monkeypatch.setattr(status_mod.shutil, "which", lambda cmd: None)
        result = status_mod._check_mcp_server()
        assert result["registered"] is False
        assert result["claude_cli"] is False

    def test_claude_not_registered(self, monkeypatch):
        monkeypatch.setattr(status_mod.shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.setattr(
            status_mod.subprocess,
            "run",
            lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})(),
        )
        result = status_mod._check_mcp_server()
        assert result["registered"] is False
        assert result["claude_cli"] is True

    def test_claude_registered_connected(self, monkeypatch):
        monkeypatch.setattr(status_mod.shutil, "which", lambda cmd: "/usr/bin/claude")
        output = "Name: somnium\nCommand: /usr/bin/somnium-mcp\nStatus: Connected\n"
        monkeypatch.setattr(
            status_mod.subprocess,
            "run",
            lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": output, "stderr": ""})(),
        )
        result = status_mod._check_mcp_server()
        assert result["registered"] is True
        assert result["connected"] is True
        assert result["command"] == "/usr/bin/somnium-mcp"

    def test_claude_timeout(self, monkeypatch):
        import subprocess

        monkeypatch.setattr(status_mod.shutil, "which", lambda cmd: "/usr/bin/claude")

        def _timeout(*a, **kw):
            raise subprocess.TimeoutExpired("claude", 5)

        monkeypatch.setattr(status_mod.subprocess, "run", _timeout)
        result = status_mod._check_mcp_server()
        assert result["registered"] is False
        assert result["claude_cli"] is True


# ---------------------------------------------------------------------------
# _short_cwd (imported from dream module but used in status context)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def test_status_basic(tmp_path, monkeypatch):
    """status command runs without crashing."""
    _patch_status(monkeypatch, tmp_path)
    # Mock out the subsection printers to avoid real file/subprocess checks
    monkeypatch.setattr(status_mod, "_print_memory_indexes", lambda c: None)
    monkeypatch.setattr(status_mod, "_print_code_index", lambda c: None)
    monkeypatch.setattr(status_mod, "_print_hooks_status", lambda: None)
    monkeypatch.setattr(status_mod, "_print_mcp_status", lambda: None)
    monkeypatch.setattr(status_mod, "_print_config_status", lambda c: None)
    monkeypatch.setattr(status_mod, "_print_statusline_tip", lambda: None)

    from somnium.cli import app

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0


def test_status_install_line(tmp_path, monkeypatch):
    """--install-line calls the installer."""
    _patch_status(monkeypatch, tmp_path)
    monkeypatch.setattr(status_mod, "_install_statusline", lambda: None)

    from somnium.cli import app

    result = runner.invoke(app, ["status", "--install-line"])
    assert result.exit_code == 0


def test_status_uninstall_line(tmp_path, monkeypatch):
    """--uninstall-line calls the uninstaller."""
    _patch_status(monkeypatch, tmp_path)
    monkeypatch.setattr(status_mod, "_uninstall_statusline", lambda: None)

    from somnium.cli import app

    result = runner.invoke(app, ["status", "--uninstall-line"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# _print_hooks_status
# ---------------------------------------------------------------------------


def test_print_hooks_no_hooks(monkeypatch, capsys):
    """No somnium hooks → prints warning."""
    monkeypatch.setattr(status_mod, "_read_somnium_hooks_from_settings", lambda: {})
    status_mod._print_hooks_status()
    # Just check it doesn't crash


def test_print_hooks_with_hooks(monkeypatch):
    """With hooks → prints checkmarks."""
    monkeypatch.setattr(
        status_mod,
        "_read_somnium_hooks_from_settings",
        lambda: {"Stop": "/bin/stop", "PostToolUse": "/bin/ptu"},
    )
    status_mod._print_hooks_status()


# ---------------------------------------------------------------------------
# _print_mcp_status
# ---------------------------------------------------------------------------


def test_print_mcp_no_cli(monkeypatch):
    monkeypatch.setattr(status_mod, "_check_mcp_server", lambda: {"claude_cli": False})
    status_mod._print_mcp_status()


def test_print_mcp_not_registered(monkeypatch):
    monkeypatch.setattr(
        status_mod,
        "_check_mcp_server",
        lambda: {"claude_cli": True, "registered": False},
    )
    status_mod._print_mcp_status()


def test_print_mcp_connected(monkeypatch):
    monkeypatch.setattr(
        status_mod,
        "_check_mcp_server",
        lambda: {"claude_cli": True, "registered": True, "connected": True, "command": "/bin/x"},
    )
    status_mod._print_mcp_status()


# ---------------------------------------------------------------------------
# _print_config_status
# ---------------------------------------------------------------------------


def test_print_config_status(tmp_path, monkeypatch):
    from somnium.config import SomniumConfig

    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path)
    status_mod._print_config_status(cfg)


def test_print_config_status_with_project(tmp_path, monkeypatch):
    from somnium.config import SomniumConfig

    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path)
    cfg.project_root = tmp_path / "myproject"
    status_mod._print_config_status(cfg)


# ---------------------------------------------------------------------------
# _print_statusline_tip
# ---------------------------------------------------------------------------


def test_statusline_tip_no_settings(tmp_path, monkeypatch):
    status_mod._print_statusline_tip()


# ---------------------------------------------------------------------------
# _install / _uninstall statusline
# ---------------------------------------------------------------------------


def test_uninstall_statusline_no_file(tmp_path, monkeypatch):
    """Uninstall when nothing exists doesn't crash."""
    status_mod._uninstall_statusline()


def test_uninstall_statusline_with_settings(tmp_path, monkeypatch):
    """Uninstall removes statusLine key from settings."""
    settings_path = Path.home() / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps({"statusLine": {"type": "command"}, "other": 1}), encoding="utf-8")

    status_mod._uninstall_statusline()

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "statusLine" not in data
    assert data["other"] == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakePath:
    """Helper that wraps a path for monkeypatching."""

    def __init__(self, p):
        self._p = p

    def home(self):
        return self._p
