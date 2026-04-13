"""Tests for the TOML writer and value formatter in cli/init.py."""

from __future__ import annotations

import tomllib

from somnium.cli.init import _toml_value, _write_toml

# ---------------------------------------------------------------------------
# _toml_value
# ---------------------------------------------------------------------------


def test_toml_value_string():
    assert _toml_value("hello") == '"hello"'


def test_toml_value_bool_true():
    assert _toml_value(True) == "true"


def test_toml_value_bool_false():
    assert _toml_value(False) == "false"


def test_toml_value_int():
    assert _toml_value(42) == "42"


def test_toml_value_float():
    assert _toml_value(3.14) == "3.14"


def test_toml_value_list_of_strings():
    assert _toml_value(["a", "b"]) == '["a", "b"]'


def test_toml_value_list_of_ints():
    assert _toml_value([1, 2, 3]) == "[1, 2, 3]"


def test_toml_value_empty_list():
    assert _toml_value([]) == "[]"


def test_toml_value_fallback_for_unknown_type():
    """Unknown types are converted to quoted strings."""
    result = _toml_value(object())
    assert result.startswith('"')
    assert result.endswith('"')


# ---------------------------------------------------------------------------
# _write_toml
# ---------------------------------------------------------------------------


def test_write_toml_flat_section(tmp_path):
    path = tmp_path / "test.toml"
    _write_toml(path, {
        "embeddings": {
            "provider": "voyage",
            "model_text": "voyage-3.5",
            "batch_size": 64,
        },
    })

    with path.open("rb") as f:
        data = tomllib.load(f)

    assert data["embeddings"]["provider"] == "voyage"
    assert data["embeddings"]["model_text"] == "voyage-3.5"
    assert data["embeddings"]["batch_size"] == 64


def test_write_toml_nested_section(tmp_path):
    path = tmp_path / "test.toml"
    _write_toml(path, {
        "dream": {
            "enabled": True,
            "gate": {
                "min_user_messages": 3,
            },
        },
    })

    with path.open("rb") as f:
        data = tomllib.load(f)

    assert data["dream"]["enabled"] is True
    assert data["dream"]["gate"]["min_user_messages"] == 3


def test_write_toml_top_level_scalars(tmp_path):
    path = tmp_path / "test.toml"
    _write_toml(path, {
        "version": 1,
        "embeddings": {"provider": "ollama"},
    })

    with path.open("rb") as f:
        data = tomllib.load(f)

    assert data["version"] == 1
    assert data["embeddings"]["provider"] == "ollama"


def test_write_toml_with_list_value(tmp_path):
    path = tmp_path / "test.toml"
    _write_toml(path, {
        "context_injection": {
            "scopes": ["project", "global", "skills"],
        },
    })

    with path.open("rb") as f:
        data = tomllib.load(f)

    assert data["context_injection"]["scopes"] == ["project", "global", "skills"]


def test_write_toml_with_bool_values(tmp_path):
    path = tmp_path / "test.toml"
    _write_toml(path, {
        "dream": {
            "enabled": True,
        },
        "code_search": {
            "semantic_enabled": False,
        },
    })

    with path.open("rb") as f:
        data = tomllib.load(f)

    assert data["dream"]["enabled"] is True
    assert data["code_search"]["semantic_enabled"] is False


def test_write_toml_roundtrip_matches_template(tmp_path):
    """Write a config that mirrors the template and verify it parses correctly."""
    path = tmp_path / "config.toml"
    _write_toml(path, {
        "embeddings": {
            "provider": "voyage",
            "model_text": "voyage-3.5",
            "model_code": "voyage-code-3",
            "api_key_env": "VOYAGE_API_KEY",
            "batch_size": 64,
        },
        "storage": {
            "global_root": "~/.claude/somnium",
            "project_marker": ".claude/somnium",
        },
        "dream": {
            "enabled": True,
            "model": "claude-sonnet-4-6",
        },
    })

    with path.open("rb") as f:
        data = tomllib.load(f)

    assert data["embeddings"]["provider"] == "voyage"
    assert data["storage"]["global_root"] == "~/.claude/somnium"
    assert data["dream"]["enabled"] is True
