"""Tests for the init onboarding wizard.

questionary prompts are monkeypatched so tests run non-interactively.
"""

from __future__ import annotations

import tomllib
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from somnium.cli import app
from somnium.cli.init import (
    _detect_model_change,
    _invalidate_indices,
    _step_code_search,
    _step_context_injection,
    _step_dream,
    _summary_code_search,
    _summary_context_injection,
    _summary_dream,
    _summary_embeddings,
    _validate_positive_int,
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
# Queue-based mock helper
# ---------------------------------------------------------------------------


def _mock_questionary(monkeypatch, *, selects=None, confirms=None, texts=None, passwords=None):
    """Set up queue-based mocks for questionary prompts.

    Each call to questionary.select/confirm/text/password pops the next
    value from the corresponding queue.
    """
    select_q = deque(selects or [])
    confirm_q = deque(confirms or [])
    text_q = deque(texts or [])
    password_q = deque(passwords or [])

    def _fake_select(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = select_q.popleft() if select_q else "keep"
        return m

    def _fake_confirm(*_args, **_kwargs):
        m = MagicMock()
        if confirm_q:
            m.ask.return_value = confirm_q.popleft()
        else:
            m.ask.return_value = _kwargs.get("default", True)
        return m

    def _fake_text(*_args, **_kwargs):
        m = MagicMock()
        if text_q:
            m.ask.return_value = text_q.popleft()
        else:
            m.ask.return_value = _kwargs.get("default", "")
        return m

    def _fake_password(*_args, **_kwargs):
        m = MagicMock()
        m.ask.return_value = password_q.popleft() if password_q else ""
        return m

    monkeypatch.setattr("somnium.cli.init.questionary.select", _fake_select)
    monkeypatch.setattr("somnium.cli.init.questionary.confirm", _fake_confirm)
    monkeypatch.setattr("somnium.cli.init.questionary.text", _fake_text)
    monkeypatch.setattr("somnium.cli.init.questionary.password", _fake_password)


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


def test_invalidate_indices_deletes_duckdb_wal(tmp_path):
    """_invalidate_indices also deletes .duckdb.wal files."""
    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "global")
    global_root = Path(cfg.storage.global_root)
    global_root.mkdir(parents=True)

    (global_root / "index.duckdb.wal").write_text("fake")
    _invalidate_indices(cfg)
    assert not (global_root / "index.duckdb.wal").exists()


# ---------------------------------------------------------------------------
# Onboarding with questionary mocked (full wizard)
# ---------------------------------------------------------------------------


def test_init_with_voyage_selection(runner, init_sandbox, monkeypatch):
    """Simulate selecting Voyage provider, defaults for steps 2-4 (fresh init)."""
    _mock_questionary(
        monkeypatch,
        selects=[
            "voyage",          # step 1: provider
            "voyage-3.5",      # step 1: model_text
            "voyage-code-3",   # step 1: model_code
            "defaults",        # step 2: skip_patterns
            "all",             # step 3: scopes
            "defaults",        # step 4: ignore list
        ],
        passwords=["pa-fake-key-12345"],
    )

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
    """Simulate selecting Ollama provider, defaults for steps 2-4 (fresh init)."""
    _mock_questionary(
        monkeypatch,
        selects=[
            "ollama",             # step 1: provider
            "nomic-embed-text",   # step 1: model_text
            "nomic-embed-text",   # step 1: model_code
            "defaults",           # step 2: skip_patterns
            "all",                # step 3: scopes
            "defaults",           # step 4: ignore list
        ],
        texts=["http://localhost:11434"],
    )
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
    """Simulate selecting 'keep' at every step."""
    # Pre-create config
    init_sandbox.mkdir(parents=True, exist_ok=True)
    (init_sandbox / "config.toml").write_text(
        '[embeddings]\nprovider = "ollama"\nmodel_text = "mxbai-embed-large"\n'
    )

    _mock_questionary(
        monkeypatch,
        selects=["keep", "keep", "keep", "keep"],
    )

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
    _mock_questionary(monkeypatch, selects=[None])

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code != 0  # typer.Abort → non-zero


def test_init_abort_on_voyage_model_selection(runner, init_sandbox, monkeypatch):
    """Cancelling the model_text prompt in Voyage aborts gracefully."""
    _mock_questionary(
        monkeypatch,
        selects=["voyage", None],  # provider → cancel on model_text
    )

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code != 0


def test_init_abort_on_voyage_api_key_prompt(runner, init_sandbox, monkeypatch):
    """Cancelling the API key prompt aborts gracefully."""
    _mock_questionary(
        monkeypatch,
        selects=["voyage", "voyage-3.5", "voyage-code-3"],
        passwords=[None],
    )

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code != 0


def test_init_abort_on_ollama_model_selection(runner, init_sandbox, monkeypatch):
    """Cancelling the model_text prompt in Ollama aborts gracefully."""
    _mock_questionary(
        monkeypatch,
        selects=["ollama", None],
        texts=["http://localhost:11434"],
    )
    monkeypatch.setattr("somnium.embeddings.ollama.check_ollama_running", lambda url: False)
    monkeypatch.setattr("somnium.embeddings.ollama.list_ollama_models", lambda url: [])

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code != 0


def test_init_abort_on_dream_step(runner, init_sandbox, monkeypatch):
    """Cancelling during dream step aborts gracefully."""
    # Fresh init → dream step goes straight to confirm (no keep/configure select).
    # We cancel on the "Enable dream mode?" confirm.
    _mock_questionary(
        monkeypatch,
        selects=[
            "voyage", "voyage-3.5", "voyage-code-3",  # step 1 OK
        ],
        passwords=["pa-key"],
        confirms=[None],  # step 2: cancel on "Enable dream mode?"
    )

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Ollama warnings / edge cases
# ---------------------------------------------------------------------------


def test_init_ollama_warns_when_not_reachable(runner, init_sandbox, monkeypatch):
    """When Ollama is not reachable, a warning should be displayed."""
    _mock_questionary(
        monkeypatch,
        selects=[
            "ollama",             # step 1: provider
            "nomic-embed-text",   # step 1: model_text
            "nomic-embed-text",   # step 1: model_code
            "defaults",           # step 2: skip_patterns
            "all",                # step 3: scopes
            "defaults",           # step 4: ignore list
        ],
        texts=["http://localhost:11434"],
    )
    monkeypatch.setattr("somnium.embeddings.ollama.check_ollama_running", lambda url: False)
    monkeypatch.setattr("somnium.embeddings.ollama.list_ollama_models", lambda url: [])

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code == 0
    assert "not reachable" in result.output or "warning" in result.output.lower()


# ---------------------------------------------------------------------------
# Validator tests
# ---------------------------------------------------------------------------


def test_validate_positive_int_valid():
    assert _validate_positive_int("5") is True
    assert _validate_positive_int("100") is True


def test_validate_positive_int_zero():
    assert isinstance(_validate_positive_int("0"), str)


def test_validate_positive_int_negative():
    assert isinstance(_validate_positive_int("-1"), str)


def test_validate_positive_int_non_numeric():
    assert isinstance(_validate_positive_int("abc"), str)


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def test_summary_embeddings():
    cfg = SomniumConfig()
    s = _summary_embeddings(cfg)
    assert "voyage" in s
    assert "voyage-3.5" in s
    assert "voyage-code-3" in s


def test_summary_dream():
    cfg = SomniumConfig()
    s = _summary_dream(cfg)
    assert "enabled" in s
    assert "claude-sonnet-4-6" in s


def test_summary_dream_with_llm_gate():
    cfg = SomniumConfig()
    cfg.dream.gate.llm_gate_enabled = True
    s = _summary_dream(cfg)
    assert "LLM gate on" in s


def test_summary_context_injection():
    cfg = SomniumConfig()
    s = _summary_context_injection(cfg)
    assert "enabled" in s
    assert "top_k=5" in s
    assert "reranker=on" in s


def test_summary_code_search():
    cfg = SomniumConfig()
    s = _summary_code_search(cfg)
    assert "symbolic=on" in s
    assert "semantic=on" in s
    assert "chunk=40" in s


# ---------------------------------------------------------------------------
# Step 2: Dream — unit tests
# ---------------------------------------------------------------------------


def test_step_dream_keep_existing(monkeypatch):
    """Selecting 'keep' returns None."""
    cfg = SomniumConfig()
    _mock_questionary(monkeypatch, selects=["keep"])
    result = _step_dream(cfg)
    assert result is None


def test_step_dream_configure_enabled(monkeypatch):
    """Full dream configuration returns correct overrides (fresh, no existing cfg)."""
    # existing_cfg=None → no "keep/configure" select
    # Order: confirm(enabled), text(model), text(min_msgs), confirm(llm_gate=False), select(patterns)
    _mock_questionary(
        monkeypatch,
        selects=["defaults"],       # skip_patterns
        confirms=[True, False],     # enabled, llm_gate
        texts=["claude-sonnet-4-6", "3"],  # model, min_msgs (no gate_model since llm_gate=False)
    )
    result = _step_dream(None)
    assert result is not None
    assert result["dream"]["enabled"] is True
    assert result["dream"]["model"] == "claude-sonnet-4-6"
    assert result["dream"]["gate"]["min_user_messages"] == 3
    assert result["dream"]["gate"]["llm_gate_enabled"] is False


def test_step_dream_disabled(monkeypatch):
    """Disabling dream returns minimal overrides (fresh, no existing cfg)."""
    _mock_questionary(
        monkeypatch,
        confirms=[False],  # enabled=False
    )
    result = _step_dream(None)
    assert result == {"dream": {"enabled": False}}


def test_step_dream_first_run_no_keep_option(monkeypatch):
    """On first run (no existing cfg), there's no 'keep' option."""
    # Order: confirm(enabled), text(model), text(min_msgs), confirm(llm_gate=True), text(gate_model), select(patterns)
    _mock_questionary(
        monkeypatch,
        confirms=[True, True],   # enabled, llm_gate
        selects=["defaults"],    # skip_patterns
        texts=["claude-sonnet-4-6", "5", "claude-haiku-4-5"],  # model, min_msgs, gate_model
    )
    result = _step_dream(None)
    assert result is not None
    assert result["dream"]["enabled"] is True
    assert result["dream"]["gate"]["llm_gate_enabled"] is True
    assert result["dream"]["gate_model"] == "claude-haiku-4-5"


def test_step_dream_skip_patterns_clear(monkeypatch):
    """Clearing skip patterns returns empty list."""
    cfg = SomniumConfig()
    cfg.dream.gate.skip_patterns = ["^commit$"]
    # Order: select(configure), confirm(enabled), text(model), text(min_msgs), confirm(llm_gate=False), select(clear)
    _mock_questionary(
        monkeypatch,
        selects=["configure", "clear"],
        confirms=[True, False],
        texts=["claude-sonnet-4-6", "3"],  # model, min_msgs (no gate_model since llm_gate=False)
    )
    result = _step_dream(cfg)
    assert result["dream"]["gate"]["skip_patterns"] == []


# ---------------------------------------------------------------------------
# Step 3: Context injection — unit tests
# ---------------------------------------------------------------------------


def test_step_context_injection_keep_existing(monkeypatch):
    cfg = SomniumConfig()
    _mock_questionary(monkeypatch, selects=["keep"])
    result = _step_context_injection(cfg)
    assert result is None


def test_step_context_injection_configure(monkeypatch):
    # existing_cfg=None → no "keep/configure" select, goes straight to prompts
    _mock_questionary(
        monkeypatch,
        selects=["all"],           # scopes
        confirms=[True, True],     # enabled, reranker
        texts=["10", "3000", "rerank-2-lite"],  # top_k, budget, reranker_model
    )
    result = _step_context_injection(None)
    assert result is not None
    ci = result["context_injection"]
    assert ci["enabled"] is True
    assert ci["top_k"] == 10
    assert ci["context_budget_tokens"] == 3000
    assert ci["scopes"] == ["project", "global", "skills"]
    assert ci["reranker_enabled"] is True


def test_step_context_injection_disabled(monkeypatch):
    _mock_questionary(
        monkeypatch,
        confirms=[False],  # enabled=False
    )
    result = _step_context_injection(None)
    assert result == {"context_injection": {"enabled": False}}


def test_step_context_injection_project_only_scope(monkeypatch):
    _mock_questionary(
        monkeypatch,
        selects=["project"],         # scopes
        confirms=[True, False],      # enabled, reranker disabled
        texts=["5", "2000"],
    )
    result = _step_context_injection(None)
    assert result["context_injection"]["scopes"] == ["project"]
    assert result["context_injection"]["reranker_enabled"] is False


# ---------------------------------------------------------------------------
# Step 4: Code search — unit tests
# ---------------------------------------------------------------------------


def test_step_code_search_keep_existing(monkeypatch):
    cfg = SomniumConfig()
    _mock_questionary(monkeypatch, selects=["keep"])
    result = _step_code_search(cfg)
    assert result is None


def test_step_code_search_configure(monkeypatch):
    # existing_cfg=None → no "keep/configure" select
    _mock_questionary(
        monkeypatch,
        selects=["defaults"],           # ignore list
        confirms=[True, True],          # symbolic, semantic
        texts=["50"],                   # chunk_lines
    )
    result = _step_code_search(None)
    assert result is not None
    cs = result["code_search"]
    assert cs["symbolic_enabled"] is True
    assert cs["semantic_enabled"] is True
    assert cs["semantic_chunk_lines"] == 50
    assert "node_modules" in cs["ignore"]


def test_step_code_search_clear_ignore(monkeypatch):
    cfg = SomniumConfig()
    _mock_questionary(
        monkeypatch,
        selects=["configure", "clear"],
        confirms=[True, False],  # symbolic, semantic disabled
    )
    result = _step_code_search(cfg)
    assert result["code_search"]["ignore"] == []
    assert result["code_search"]["semantic_enabled"] is False


# ---------------------------------------------------------------------------
# Full wizard integration (through CLI runner)
# ---------------------------------------------------------------------------


def test_init_full_wizard_all_steps(runner, init_sandbox, monkeypatch):
    """Full wizard completing all 4 steps writes correct config (fresh init)."""
    # Fresh init: no existing config → no "keep/configure" selects for steps 2-4.
    _mock_questionary(
        monkeypatch,
        selects=[
            "voyage",            # step 1: provider
            "voyage-3.5",        # step 1: model_text
            "voyage-code-3",     # step 1: model_code
            "defaults",          # step 2: skip_patterns
            "all",               # step 3: scopes
            "defaults",          # step 4: ignore list
        ],
        confirms=[
            True,   # step 2: dream enabled
            False,  # step 2: llm_gate
            True,   # step 3: context injection enabled
            True,   # step 3: reranker
            True,   # step 4: symbolic
            True,   # step 4: semantic
        ],
        texts=[
            "claude-sonnet-4-6",   # step 2: model
            "3",                   # step 2: min_user_messages (no gate_model since llm_gate=False)
            "5",                   # step 3: top_k
            "2000",                # step 3: budget
            "rerank-2-lite",       # step 3: reranker_model
            "40",                  # step 4: chunk_lines
        ],
        passwords=["pa-fake-key"],
    )

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code == 0

    config_path = init_sandbox / "config.toml"
    with config_path.open("rb") as f:
        data = tomllib.load(f)
    assert data["embeddings"]["provider"] == "voyage"
    assert data["dream"]["enabled"] is True
    assert data["dream"]["gate"]["min_user_messages"] == 3
    assert data["context_injection"]["enabled"] is True
    assert data["context_injection"]["reranker_enabled"] is True
    assert data["code_search"]["symbolic_enabled"] is True


def test_init_keep_all_four_steps(runner, init_sandbox, monkeypatch):
    """Choosing 'keep' at every step does not modify config."""
    init_sandbox.mkdir(parents=True, exist_ok=True)
    (init_sandbox / "config.toml").write_text(
        '[embeddings]\nprovider = "voyage"\nmodel_text = "voyage-3.5"\n'
    )

    _mock_questionary(
        monkeypatch,
        selects=["keep", "keep", "keep", "keep"],
    )

    result = runner.invoke(app, ["init", "--skip-hooks"])
    assert result.exit_code == 0

    with (init_sandbox / "config.toml").open("rb") as f:
        data = tomllib.load(f)
    # Original config unchanged
    assert data["embeddings"]["provider"] == "voyage"
    assert "dream" not in data  # never written since we kept
