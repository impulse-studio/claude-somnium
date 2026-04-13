"""Tests for the init onboarding wizard.

questionary prompts are monkeypatched so tests run non-interactively.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from somnium.cli import app
from somnium.cli.init import (
    _detect_model_change,
    _invalidate_indices,
)
from somnium.config import SomniumConfig


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def init_sandbox(tmp_path, monkeypatch):
    """Isolated sandbox for init tests."""
    home = tmp_path / "home"
    home.mkdir()
    somnium_home = home / ".claude" / "somnium"

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SOMNIUM_HOME", str(somnium_home))
    monkeypatch.setenv("VOYAGE_API_KEY", "fake-key")

    from somnium.config import reset_config_cache
    reset_config_cache()

    from somnium.hooks import install as install_module
    monkeypatch.setattr(install_module, "_install_mcp_server", lambda: "+ mcp")
    monkeypatch.setattr(install_module, "_uninstall_mcp_server", lambda: "- mcp")
    monkeypatch.setattr(install_module, "SETTINGS_PATH", home / ".claude" / "settings.json")
    monkeypatch.setattr(install_module, "_resolve_bin", lambda name: f"/fake/{name}")

    return somnium_home


# ---------------------------------------------------------------------------
# Non-interactive mode (--non-interactive)
# ---------------------------------------------------------------------------


def test_init_non_interactive_creates_dirs(runner, init_sandbox):
    result = runner.invoke(app, ["init", "--non-interactive", "--skip-hooks"])
    assert result.exit_code == 0
    assert (init_sandbox / "memory").is_dir()
    assert (init_sandbox / "skills").is_dir()
    assert (init_sandbox / "config.toml").exists()


def test_init_non_interactive_keeps_existing_config(runner, init_sandbox):
    """--non-interactive should not overwrite an existing config."""
    # Create initial config
    init_sandbox.mkdir(parents=True, exist_ok=True)
    config_path = init_sandbox / "config.toml"
    config_path.write_text('[embeddings]\nprovider = "ollama"\nmodel_text = "nomic-embed-text"\n')

    result = runner.invoke(app, ["init", "--non-interactive", "--skip-hooks"])
    assert result.exit_code == 0

    # Config should be unchanged (still ollama)
    with config_path.open("rb") as f:
        data = tomllib.load(f)
    assert data["embeddings"]["provider"] == "ollama"


# ---------------------------------------------------------------------------
# Model change detection
# ---------------------------------------------------------------------------


def test_detect_model_change_no_old_config():
    assert _detect_model_change(None, {"embeddings": {"provider": "voyage"}}) is False


def test_detect_model_change_same_model():
    cfg = SomniumConfig()
    cfg.embeddings.provider = "voyage"
    cfg.embeddings.model_text = "voyage-3.5"
    cfg.embeddings.model_code = "voyage-code-3"
    overrides = {"embeddings": {"provider": "voyage", "model_text": "voyage-3.5", "model_code": "voyage-code-3"}}
    assert _detect_model_change(cfg, overrides) is False


def test_detect_model_change_provider_changed():
    cfg = SomniumConfig()
    cfg.embeddings.provider = "voyage"
    overrides = {"embeddings": {"provider": "ollama"}}
    assert _detect_model_change(cfg, overrides) is True


def test_detect_model_change_model_text_changed():
    cfg = SomniumConfig()
    cfg.embeddings.provider = "ollama"
    cfg.embeddings.model_text = "nomic-embed-text"
    overrides = {"embeddings": {"provider": "ollama", "model_text": "mxbai-embed-large"}}
    assert _detect_model_change(cfg, overrides) is True


def test_detect_model_change_model_code_changed():
    cfg = SomniumConfig()
    cfg.embeddings.provider = "voyage"
    cfg.embeddings.model_code = "voyage-code-3"
    overrides = {"embeddings": {"model_code": "voyage-3.5"}}
    assert _detect_model_change(cfg, overrides) is True


# ---------------------------------------------------------------------------
# Index invalidation
# ---------------------------------------------------------------------------


def test_invalidate_indices_deletes_parquet_and_duckdb(tmp_path):
    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "global")
    global_root = Path(cfg.storage.global_root)
    global_root.mkdir(parents=True)

    # Create fake index files
    (global_root / "index.parquet").write_text("fake")
    (global_root / "index.duckdb").write_text("fake")

    _invalidate_indices(cfg)

    assert not (global_root / "index.parquet").exists()
    assert not (global_root / "index.duckdb").exists()


def test_invalidate_indices_handles_missing_dirs():
    cfg = SomniumConfig()
    cfg.storage.global_root = "/nonexistent/path/that/doesnt/exist"
    # Should not raise
    _invalidate_indices(cfg)


# ---------------------------------------------------------------------------
# Onboarding with questionary mocked
# ---------------------------------------------------------------------------


def test_init_with_voyage_selection(runner, init_sandbox, monkeypatch):
    """Simulate selecting Voyage provider in onboarding."""
    call_count = {"select": 0}

    def _fake_select(question, choices=None, default=None):
        call_count["select"] += 1
        m = MagicMock()
        if call_count["select"] == 1:
            m.ask.return_value = "voyage"  # provider
        elif call_count["select"] == 2:
            m.ask.return_value = "voyage-3.5"  # model_text
        elif call_count["select"] == 3:
            m.ask.return_value = "voyage-code-3"  # model_code
        return m

    def _fake_password(question):
        m = MagicMock()
        m.ask.return_value = "pa-fake-key-12345"
        return m

    monkeypatch.setattr("somnium.cli.init.questionary.select", _fake_select)
    monkeypatch.setattr("somnium.cli.init.questionary.password", _fake_password)

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code == 0

    config_path = init_sandbox / "config.toml"
    assert config_path.exists()
    with config_path.open("rb") as f:
        data = tomllib.load(f)
    assert data["embeddings"]["provider"] == "voyage"
    assert data["embeddings"]["model_text"] == "voyage-3.5"
    assert data["embeddings"]["api_key"] == "pa-fake-key-12345"


def test_init_with_ollama_selection(runner, init_sandbox, monkeypatch):
    """Simulate selecting Ollama provider in onboarding."""
    call_count = {"select": 0}

    def _fake_select(question, choices=None, default=None):
        call_count["select"] += 1
        m = MagicMock()
        if call_count["select"] == 1:
            m.ask.return_value = "ollama"  # provider
        elif call_count["select"] == 2:
            m.ask.return_value = "nomic-embed-text"  # model_text
        elif call_count["select"] == 3:
            m.ask.return_value = "nomic-embed-text"  # model_code
        return m

    def _fake_text(question, default=None):
        m = MagicMock()
        m.ask.return_value = default or "http://localhost:11434"
        return m

    monkeypatch.setattr("somnium.cli.init.questionary.select", _fake_select)
    monkeypatch.setattr("somnium.cli.init.questionary.text", _fake_text)
    monkeypatch.setattr("somnium.embeddings.ollama.check_ollama_running", lambda url: False)
    monkeypatch.setattr("somnium.embeddings.ollama.list_ollama_models", lambda url: [])

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code == 0

    config_path = init_sandbox / "config.toml"
    with config_path.open("rb") as f:
        data = tomllib.load(f)
    assert data["embeddings"]["provider"] == "ollama"
    assert data["embeddings"]["model_text"] == "nomic-embed-text"


def test_init_keep_existing(runner, init_sandbox, monkeypatch):
    """Simulate selecting 'Use existing settings'."""
    # Pre-create config
    init_sandbox.mkdir(parents=True, exist_ok=True)
    (init_sandbox / "config.toml").write_text(
        '[embeddings]\nprovider = "ollama"\nmodel_text = "mxbai-embed-large"\n'
    )

    def _fake_select(question, choices=None, default=None):
        m = MagicMock()
        m.ask.return_value = "keep"
        return m

    monkeypatch.setattr("somnium.cli.init.questionary.select", _fake_select)

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code == 0

    # Config unchanged
    with (init_sandbox / "config.toml").open("rb") as f:
        data = tomllib.load(f)
    assert data["embeddings"]["provider"] == "ollama"
    assert data["embeddings"]["model_text"] == "mxbai-embed-large"


# ---------------------------------------------------------------------------
# Abort paths (user cancels with Ctrl-C / None from questionary)
# ---------------------------------------------------------------------------


def test_init_abort_on_provider_selection(runner, init_sandbox, monkeypatch):
    """Cancelling the provider prompt aborts gracefully."""
    def _fake_select(question, choices=None, default=None):
        m = MagicMock()
        m.ask.return_value = None  # user pressed Ctrl-C
        return m

    monkeypatch.setattr("somnium.cli.init.questionary.select", _fake_select)

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code != 0  # typer.Abort → non-zero


def test_init_abort_on_voyage_model_selection(runner, init_sandbox, monkeypatch):
    """Cancelling the model_text prompt in Voyage aborts gracefully."""
    call_count = {"select": 0}

    def _fake_select(question, choices=None, default=None):
        call_count["select"] += 1
        m = MagicMock()
        if call_count["select"] == 1:
            m.ask.return_value = "voyage"  # provider
        else:
            m.ask.return_value = None  # cancel on model_text
        return m

    monkeypatch.setattr("somnium.cli.init.questionary.select", _fake_select)

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code != 0


def test_init_abort_on_voyage_api_key_prompt(runner, init_sandbox, monkeypatch):
    """Cancelling the API key prompt aborts gracefully."""
    call_count = {"select": 0}

    def _fake_select(question, choices=None, default=None):
        call_count["select"] += 1
        m = MagicMock()
        if call_count["select"] == 1:
            m.ask.return_value = "voyage"
        elif call_count["select"] == 2:
            m.ask.return_value = "voyage-3.5"
        elif call_count["select"] == 3:
            m.ask.return_value = "voyage-code-3"
        return m

    def _fake_password(question):
        m = MagicMock()
        m.ask.return_value = None  # cancel on password prompt
        return m

    monkeypatch.setattr("somnium.cli.init.questionary.select", _fake_select)
    monkeypatch.setattr("somnium.cli.init.questionary.password", _fake_password)

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code != 0


def test_init_abort_on_ollama_model_selection(runner, init_sandbox, monkeypatch):
    """Cancelling the model_text prompt in Ollama aborts gracefully."""
    call_count = {"select": 0}

    def _fake_select(question, choices=None, default=None):
        call_count["select"] += 1
        m = MagicMock()
        if call_count["select"] == 1:
            m.ask.return_value = "ollama"
        else:
            m.ask.return_value = None  # cancel on model selection
        return m

    monkeypatch.setattr("somnium.cli.init.questionary.select", _fake_select)
    monkeypatch.setattr("somnium.embeddings.ollama.check_ollama_running", lambda url: False)
    monkeypatch.setattr("somnium.embeddings.ollama.list_ollama_models", lambda url: [])

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Ollama warnings / edge cases
# ---------------------------------------------------------------------------


def test_init_ollama_warns_when_not_reachable(runner, init_sandbox, monkeypatch, capsys):
    """When Ollama is not reachable, a warning should be displayed."""
    call_count = {"select": 0}

    def _fake_select(question, choices=None, default=None):
        call_count["select"] += 1
        m = MagicMock()
        if call_count["select"] == 1:
            m.ask.return_value = "ollama"
        elif call_count["select"] == 2 or call_count["select"] == 3:
            m.ask.return_value = "nomic-embed-text"
        return m

    def _fake_text(question, default=None):
        m = MagicMock()
        m.ask.return_value = default or "http://localhost:11434"
        return m

    monkeypatch.setattr("somnium.cli.init.questionary.select", _fake_select)
    monkeypatch.setattr("somnium.cli.init.questionary.text", _fake_text)
    monkeypatch.setattr("somnium.embeddings.ollama.check_ollama_running", lambda url: False)
    monkeypatch.setattr("somnium.embeddings.ollama.list_ollama_models", lambda url: [])

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code == 0
    assert "not reachable" in result.output or "warning" in result.output.lower()


def test_invalidate_indices_deletes_duckdb_wal(tmp_path):
    """_invalidate_indices also deletes .duckdb.wal files."""
    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "global")
    global_root = Path(cfg.storage.global_root)
    global_root.mkdir(parents=True)

    (global_root / "index.duckdb.wal").write_text("fake")
    _invalidate_indices(cfg)
    assert not (global_root / "index.duckdb.wal").exists()
