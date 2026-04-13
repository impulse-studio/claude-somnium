"""Tests for ``somnium update`` command and helpers."""

from __future__ import annotations

from typer.testing import CliRunner

from somnium.cli import update as update_mod

runner = CliRunner()


def test_detect_uv(monkeypatch):
    monkeypatch.setattr(update_mod.shutil, "which", lambda cmd: "/usr/bin/uv" if cmd == "uv" else None)
    monkeypatch.setattr(update_mod.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "claude-somnium 0.2.0"})())
    assert update_mod._detect_installer("claude-somnium") == "uv"


def test_detect_pipx(monkeypatch):
    monkeypatch.setattr(update_mod.shutil, "which", lambda cmd: "/usr/bin/pipx" if cmd == "pipx" else None)
    monkeypatch.setattr(update_mod.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "claude-somnium 0.2.0"})())
    assert update_mod._detect_installer("claude-somnium") == "pipx"


def test_detect_none(monkeypatch):
    monkeypatch.setattr(update_mod.shutil, "which", lambda cmd: None)
    assert update_mod._detect_installer("claude-somnium") is None


def test_detect_uv_timeout(monkeypatch):
    """uv exists but times out → falls through."""
    import subprocess

    monkeypatch.setattr(update_mod.shutil, "which", lambda cmd: "/usr/bin/uv" if cmd == "uv" else None)
    monkeypatch.setattr(update_mod.subprocess, "run", _raise(subprocess.TimeoutExpired("uv", 10)))
    assert update_mod._detect_installer("claude-somnium") is None


def test_detect_pipx_timeout(monkeypatch):
    """pipx exists but times out → returns None."""
    import subprocess

    monkeypatch.setattr(update_mod.shutil, "which", lambda cmd: "/usr/bin/pipx" if cmd == "pipx" else None)
    monkeypatch.setattr(update_mod.subprocess, "run", _raise(subprocess.TimeoutExpired("pipx", 10)))
    assert update_mod._detect_installer("claude-somnium") is None


def test_detect_uv_not_listed(monkeypatch):
    """uv exists but package not in list → falls through to pipx."""
    monkeypatch.setattr(update_mod.shutil, "which", lambda cmd: "/usr/bin/uv" if cmd == "uv" else None)
    monkeypatch.setattr(update_mod.subprocess, "run", lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "other-pkg 1.0"})())
    assert update_mod._detect_installer("claude-somnium") is None


# --- CLI command tests ---


def test_update_no_manager(monkeypatch):
    """update exits 1 when no installer detected."""
    from somnium.cli import app

    monkeypatch.setattr(update_mod, "_detect_installer", lambda pkg: None)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
    assert "could not detect" in result.output


def test_update_uv_success(monkeypatch):
    """update succeeds with uv."""
    from somnium.cli import app

    monkeypatch.setattr(update_mod, "_detect_installer", lambda pkg: "uv")
    monkeypatch.setattr(
        update_mod.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "upgraded", "stderr": ""})(),
    )
    monkeypatch.setattr(update_mod, "_reinit", lambda: None)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "upgrade complete" in result.output


def test_update_upgrade_fails(monkeypatch):
    """update exits 1 when subprocess fails."""
    from somnium.cli import app

    monkeypatch.setattr(update_mod, "_detect_installer", lambda pkg: "pipx")
    monkeypatch.setattr(
        update_mod.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": "network error"})(),
    )
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
    assert "upgrade failed" in result.output


def test_update_skip_init(monkeypatch):
    """--skip-init skips hook re-registration."""
    from somnium.cli import app

    monkeypatch.setattr(update_mod, "_detect_installer", lambda pkg: "uv")
    monkeypatch.setattr(
        update_mod.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "done", "stderr": ""})(),
    )
    reregister_called = {"v": False}
    original = update_mod._reinit

    def _track():
        reregister_called["v"] = True
        original()

    monkeypatch.setattr(update_mod, "_reinit", _track)
    result = runner.invoke(app, ["update", "--skip-init"])
    assert result.exit_code == 0
    assert not reregister_called["v"]


def test_reinit_handles_exception(monkeypatch):
    """_reinit catches install errors gracefully."""
    from somnium.config import SomniumConfig

    monkeypatch.setattr(update_mod, "reset_config_cache", lambda: None)
    monkeypatch.setattr(update_mod, "load_config", SomniumConfig)
    monkeypatch.setattr("somnium.cli.init._setup_global", lambda cfg, force: None)
    monkeypatch.setattr(update_mod, "install_hooks", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    # Should not raise
    update_mod._reinit()


def _raise(exc):
    """Return a callable that raises the given exception."""
    def _fn(*args, **kwargs):
        raise exc
    return _fn
