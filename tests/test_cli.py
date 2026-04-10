"""Smoke tests for the typer CLI.

These never touch the user's real ~/.claude directory — every test
gets its own SOMNIUM_HOME and HOME pointing at a tmp_path. We patch
the embedder so no Voyage API calls are made, and we patch the MCP
install helpers so no `claude mcp add` subprocess is spawned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from somnium import indexer as memory_indexer
from somnium.cli import app
from somnium.embeddings.voyage import EmbedResult


class _FakeEmbedder:
    def embed(self, texts, *, kind="text", input_type="document"):
        return EmbedResult(
            embeddings=[[1.0, 0.0, 0.0, 0.0] for _ in texts],
            model="fake",
            input_type=input_type,
        )

    def embed_query(self, text, *, kind="text"):
        return [1.0, 0.0, 0.0, 0.0]

    def model_for(self, kind):
        return "fake"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cli_sandbox(tmp_path, monkeypatch):
    """Isolate everything: SOMNIUM_HOME, HOME, embedder, MCP install."""
    home = tmp_path / "home"
    home.mkdir()
    somnium_home = home / ".claude" / "somnium"

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SOMNIUM_HOME", str(somnium_home))
    monkeypatch.setenv("VOYAGE_API_KEY", "fake-key-not-used")

    # Reset any cached config so the sandbox env is picked up
    from somnium.config import reset_config_cache

    reset_config_cache()

    # Patch the install module so we don't shell out to `claude mcp add`
    from somnium.hooks import install as install_module

    monkeypatch.setattr(
        install_module,
        "_install_mcp_server",
        lambda: "+ mcpServers.somnium -> /fake/path/somnium-mcp",
    )
    monkeypatch.setattr(
        install_module,
        "_uninstall_mcp_server",
        lambda: "- mcpServers.somnium",
    )
    # Redirect SETTINGS_PATH so we don't touch the real ~/.claude/settings.json
    monkeypatch.setattr(
        install_module, "SETTINGS_PATH", home / ".claude" / "settings.json"
    )
    # And resolve_bin returns a stable string for hooks
    monkeypatch.setattr(
        install_module,
        "_resolve_bin",
        lambda name: f"/fake/path/{name}",
    )

    # Patch the embedder for any command that needs it (search, index, dream)
    monkeypatch.setattr(memory_indexer, "get_embedder", lambda c=None: _FakeEmbedder())

    return somnium_home


# ---------------------------------------------------------------------------
# version + help
# ---------------------------------------------------------------------------


def test_version_command(runner, cli_sandbox):
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "somnium" in result.stdout.lower()


def test_help_lists_main_commands(runner, cli_sandbox):
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("init", "index", "status", "search", "dream", "uninstall"):
        assert cmd in result.stdout


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_creates_global_folders(runner, cli_sandbox):
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (cli_sandbox / "memory").is_dir()
    assert (cli_sandbox / "skills").is_dir()
    assert (cli_sandbox / "dream" / "sessions").is_dir()
    assert (cli_sandbox / "config.toml").exists()


def test_init_writes_config_template(runner, cli_sandbox):
    runner.invoke(app, ["init"])
    config = (cli_sandbox / "config.toml").read_text()
    assert "[embeddings]" in config
    assert "voyage" in config


def test_init_is_idempotent(runner, cli_sandbox):
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    # Second run reports "already installed" for the hooks
    assert "already" in result.stdout or "kept" in result.stdout


def test_init_skip_hooks_does_not_register_anything(runner, cli_sandbox):
    """--skip-hooks must short-circuit the hook installer."""
    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code == 0
    # No "Registering hooks" line in output
    assert "Registering hooks" not in result.stdout


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_runs_after_init(runner, cli_sandbox):
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "global" in result.stdout
    assert "Voyage" in result.stdout
    assert "Dream mode" in result.stdout


def test_status_reports_voyage_key_set(runner, cli_sandbox):
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["status"])
    assert "set" in result.stdout


# ---------------------------------------------------------------------------
# index — friendly error when no key
# ---------------------------------------------------------------------------


def test_index_friendly_error_without_voyage_key(runner, cli_sandbox, monkeypatch):
    """Without VOYAGE_API_KEY, index must produce a clean error, not a traceback."""
    runner.invoke(app, ["init"])
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    from somnium.config import reset_config_cache

    reset_config_cache()

    result = runner.invoke(app, ["index"])
    assert result.exit_code == 1
    assert "Voyage" in result.stdout
    # No raw traceback
    assert "Traceback" not in result.stdout


def test_index_runs_with_no_memories(runner, cli_sandbox):
    """Index on an empty memory dir is a no-op, exit 0."""
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["index"])
    assert result.exit_code == 0
    assert "embedded 0" in result.stdout


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def test_uninstall_removes_hooks(runner, cli_sandbox):
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["uninstall"])
    assert result.exit_code == 0
    assert "done" in result.stdout.lower() or "PostToolUse" in result.stdout

    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text())
        # Either no hooks key, or no Somnium-marked entries left
        for event in data.get("hooks", {}).values():
            for group in event:
                assert not group.get("_somnium")


def test_uninstall_keeps_data_by_default(runner, cli_sandbox):
    runner.invoke(app, ["init"])
    # Drop a fake memory file to verify it survives uninstall
    (cli_sandbox / "memory" / "keep.md").write_text("# keep me")
    runner.invoke(app, ["uninstall"])
    assert (cli_sandbox / "memory" / "keep.md").exists()


# ---------------------------------------------------------------------------
# install-hooks
# ---------------------------------------------------------------------------


def test_install_hooks_command(runner, cli_sandbox):
    """install-hooks exists as a separate command for re-registering."""
    result = runner.invoke(app, ["install-hooks"])
    assert result.exit_code == 0
