"""Tests for the `somnium config` helper functions and CLI commands."""

from __future__ import annotations

from typer.testing import CliRunner

from somnium.cli.config import (
    _is_secret_key,
    _load_toml,
    _resolve_key,
    _serialize_toml,
    _set_key,
    _toml_value,
    _write_toml,
)

# ---------------------------------------------------------------------------
# _resolve_key
# ---------------------------------------------------------------------------


class TestResolveKey:
    def test_nested_key(self):
        data = {"dream": {"model": "claude-haiku-4-5", "enabled": True}}
        assert _resolve_key(data, "dream.model") == "claude-haiku-4-5"

    def test_missing_key_returns_none(self):
        data = {"dream": {"model": "claude-haiku-4-5"}}
        assert _resolve_key(data, "dream.missing") is None

    def test_missing_intermediate_key_returns_none(self):
        data = {"dream": {"model": "claude-haiku-4-5"}}
        assert _resolve_key(data, "no.such.path") is None

    def test_top_level_key(self):
        data = {"version": 2, "dream": {}}
        assert _resolve_key(data, "version") == 2

    def test_deeply_nested_key(self):
        data = {"a": {"b": {"c": {"d": 42}}}}
        assert _resolve_key(data, "a.b.c.d") == 42

    def test_returns_dict_for_section_key(self):
        data = {"dream": {"model": "x", "enabled": True}}
        assert _resolve_key(data, "dream") == {"model": "x", "enabled": True}

    def test_non_dict_intermediate_returns_none(self):
        """If an intermediate segment resolves to a non-dict, return None."""
        data = {"dream": "scalar"}
        assert _resolve_key(data, "dream.model") is None


# ---------------------------------------------------------------------------
# _set_key
# ---------------------------------------------------------------------------


class TestSetKey:
    def test_set_nested_key(self):
        data: dict = {}
        _set_key(data, "dream.model", "claude-haiku-4-5")
        assert data == {"dream": {"model": "claude-haiku-4-5"}}

    def test_auto_parse_bool_true(self):
        data: dict = {}
        _set_key(data, "dream.enabled", "true")
        assert data["dream"]["enabled"] is True

    def test_auto_parse_bool_false(self):
        data: dict = {}
        _set_key(data, "dream.enabled", "false")
        assert data["dream"]["enabled"] is False

    def test_auto_parse_bool_case_insensitive(self):
        data: dict = {}
        _set_key(data, "a", "True")
        assert data["a"] is True
        _set_key(data, "b", "FALSE")
        assert data["b"] is False

    def test_auto_parse_int(self):
        data: dict = {}
        _set_key(data, "context_injection.top_k", "10")
        assert data["context_injection"]["top_k"] == 10
        assert isinstance(data["context_injection"]["top_k"], int)

    def test_auto_parse_float(self):
        data: dict = {}
        _set_key(data, "threshold", "0.75")
        assert data["threshold"] == 0.75
        assert isinstance(data["threshold"], float)

    def test_string_value_preserved(self):
        data: dict = {}
        _set_key(data, "dream.model", "claude-haiku-4-5")
        assert data["dream"]["model"] == "claude-haiku-4-5"
        assert isinstance(data["dream"]["model"], str)

    def test_creates_intermediate_dicts(self):
        data: dict = {}
        _set_key(data, "a.b.c", "deep")
        assert data == {"a": {"b": {"c": "deep"}}}

    def test_overwrites_existing_value(self):
        data = {"dream": {"model": "old"}}
        _set_key(data, "dream.model", "new")
        assert data["dream"]["model"] == "new"

    def test_overwrites_non_dict_intermediate(self):
        """If an intermediate is a non-dict scalar, _set_key replaces it."""
        data: dict = {"dream": "scalar"}
        _set_key(data, "dream.model", "x")
        assert data["dream"]["model"] == "x"

    def test_returns_original_dict(self):
        data: dict = {"k": "v"}
        result = _set_key(data, "k", "new")
        assert result is data


# ---------------------------------------------------------------------------
# _toml_value
# ---------------------------------------------------------------------------


class TestTomlValue:
    def test_bool_true(self):
        assert _toml_value(True) == "true"

    def test_bool_false(self):
        assert _toml_value(False) == "false"

    def test_int(self):
        assert _toml_value(42) == "42"

    def test_float(self):
        assert _toml_value(3.14) == "3.14"

    def test_string(self):
        assert _toml_value("hello") == '"hello"'

    def test_string_with_quotes(self):
        # The function wraps in double quotes
        assert _toml_value("it's") == '"it\'s"'

    def test_list_of_strings(self):
        result = _toml_value(["a", "b", "c"])
        assert result == '["a", "b", "c"]'

    def test_list_of_ints(self):
        result = _toml_value([1, 2, 3])
        assert result == "[1, 2, 3]"

    def test_list_mixed(self):
        result = _toml_value(["x", 1])
        assert result == '["x", 1]'

    def test_empty_list(self):
        assert _toml_value([]) == "[]"

    def test_bool_not_treated_as_int(self):
        """bool is a subclass of int; ensure True maps to 'true', not '1'."""
        assert _toml_value(True) == "true"
        assert _toml_value(False) == "false"


# ---------------------------------------------------------------------------
# _is_secret_key
# ---------------------------------------------------------------------------


class TestIsSecretKey:
    def test_api_key(self):
        assert _is_secret_key("api_key") is True

    def test_api_key_in_dotted_path(self):
        assert _is_secret_key("embeddings.api_key") is True

    def test_token(self):
        assert _is_secret_key("auth_token") is True

    def test_secret(self):
        assert _is_secret_key("client_secret") is True

    def test_model_not_secret(self):
        assert _is_secret_key("model") is False

    def test_enabled_not_secret(self):
        assert _is_secret_key("dream.enabled") is False

    def test_case_insensitive(self):
        assert _is_secret_key("API_KEY") is True
        assert _is_secret_key("Token") is True
        assert _is_secret_key("SECRET") is True


# -- _write_toml + _load_toml (roundtrip) --


class TestWriteLoadRoundtrip:
    def test_roundtrip_flat(self, tmp_path):
        path = tmp_path / "config.toml"
        data = {"model": "claude-haiku-4-5", "enabled": True, "top_k": 5}
        _write_toml(path, data)
        loaded = _load_toml(path)
        assert loaded == data

    def test_roundtrip_nested(self, tmp_path):
        path = tmp_path / "config.toml"
        data = {
            "dream": {"model": "claude-haiku-4-5", "enabled": True},
            "embeddings": {"provider": "voyage", "model_code": "voyage-3"},
        }
        _write_toml(path, data)
        loaded = _load_toml(path)
        assert loaded == data

    def test_roundtrip_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "a" / "b" / "config.toml"
        _write_toml(path, {"key": "val"})
        assert path.exists()
        assert _load_toml(path) == {"key": "val"}

    def test_load_missing_file_returns_empty_dict(self, tmp_path):
        path = tmp_path / "does_not_exist.toml"
        assert _load_toml(path) == {}

    def test_roundtrip_with_int_and_float(self, tmp_path):
        path = tmp_path / "config.toml"
        data = {"count": 10, "threshold": 0.85}
        _write_toml(path, data)
        loaded = _load_toml(path)
        assert loaded["count"] == 10
        assert loaded["threshold"] == 0.85

    def test_roundtrip_deeply_nested(self, tmp_path):
        path = tmp_path / "config.toml"
        data = {"a": {"b": {"c": "deep"}}}
        _write_toml(path, data)
        loaded = _load_toml(path)
        assert loaded == data


# ---------------------------------------------------------------------------
# _serialize_toml
# ---------------------------------------------------------------------------


class TestSerializeToml:
    def test_flat_dict(self):
        lines: list[str] = []
        _serialize_toml({"model": "claude", "count": 3}, lines, prefix="")
        text = "\n".join(lines)
        assert 'model = "claude"' in text
        assert "count = 3" in text

    def test_nested_dict_produces_section_header(self):
        lines: list[str] = []
        _serialize_toml({"dream": {"model": "claude"}}, lines, prefix="")
        text = "\n".join(lines)
        assert "[dream]" in text
        assert 'model = "claude"' in text

    def test_multiple_sections(self):
        lines: list[str] = []
        data = {
            "dream": {"model": "claude"},
            "embeddings": {"provider": "voyage"},
        }
        _serialize_toml(data, lines, prefix="")
        text = "\n".join(lines)
        assert "[dream]" in text
        assert "[embeddings]" in text

    def test_deeply_nested_section(self):
        lines: list[str] = []
        data = {"a": {"b": {"c": "val"}}}
        _serialize_toml(data, lines, prefix="")
        text = "\n".join(lines)
        assert "[a]" in text
        assert "[a.b]" in text
        assert 'c = "val"' in text

    def test_empty_dict_produces_no_lines(self):
        lines: list[str] = []
        _serialize_toml({}, lines, prefix="")
        assert lines == []

    def test_bool_values(self):
        lines: list[str] = []
        _serialize_toml({"enabled": True, "debug": False}, lines, prefix="")
        text = "\n".join(lines)
        assert "enabled = true" in text
        assert "debug = false" in text


# ---------------------------------------------------------------------------
# CLI command tests (via CliRunner)
# ---------------------------------------------------------------------------

runner = CliRunner()


def _patch_config(monkeypatch, tmp_path):
    """Patch config so config commands use a tmp sandbox."""
    from somnium.cli import config as cfg_mod
    from somnium.config import SomniumConfig

    somnium_home = tmp_path / "home"
    somnium_home.mkdir()
    (somnium_home / "config.toml").write_text(
        '[embeddings]\napi_key = "pa-fake-key-for-tests"\n',
        encoding="utf-8",
    )
    cfg = SomniumConfig()
    cfg.storage.global_root = str(somnium_home)
    cfg.project_root = None

    monkeypatch.setattr(cfg_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(cfg_mod, "reset_config_cache", lambda: None)
    return somnium_home, cfg


def test_config_get_effective(tmp_path, monkeypatch):
    from somnium.cli.config import config_app

    _patch_config(monkeypatch, tmp_path)
    result = runner.invoke(config_app, ["get", "embeddings.provider"])
    assert result.exit_code == 0


def test_config_get_not_set(tmp_path, monkeypatch):
    from somnium.cli.config import config_app

    _patch_config(monkeypatch, tmp_path)
    result = runner.invoke(config_app, ["get", "nonexistent.key"])
    assert result.exit_code == 0
    assert "not set" in result.output


def test_config_get_secret_masked(tmp_path, monkeypatch):
    from somnium.cli.config import config_app

    somnium_home, _ = _patch_config(monkeypatch, tmp_path)
    result = runner.invoke(config_app, ["get", "embeddings.api_key", "--scope", "global"])
    assert result.exit_code == 0
    assert "pa-fak" in result.output
    assert "ests" in result.output


def test_config_get_section(tmp_path, monkeypatch):
    from somnium.cli.config import config_app

    _patch_config(monkeypatch, tmp_path)
    result = runner.invoke(config_app, ["get", "embeddings", "--scope", "global"])
    assert result.exit_code == 0


def test_config_set_global(tmp_path, monkeypatch):
    from somnium.cli.config import config_app

    _patch_config(monkeypatch, tmp_path)
    result = runner.invoke(config_app, ["set", "dream.model", "claude-haiku-4-5"])
    assert result.exit_code == 0
    assert "Set" in result.output


def test_config_set_effective_fails(tmp_path, monkeypatch):
    from somnium.cli.config import config_app

    _patch_config(monkeypatch, tmp_path)
    result = runner.invoke(config_app, ["set", "dream.model", "x", "--scope", "effective"])
    assert result.exit_code == 1


def test_config_list_effective(tmp_path, monkeypatch):
    from somnium.cli.config import config_app

    _patch_config(monkeypatch, tmp_path)
    result = runner.invoke(config_app, ["list"])
    assert result.exit_code == 0


def test_config_list_global(tmp_path, monkeypatch):
    from somnium.cli.config import config_app

    _patch_config(monkeypatch, tmp_path)
    result = runner.invoke(config_app, ["list", "--scope", "global"])
    assert result.exit_code == 0


def test_config_list_empty_scope(tmp_path, monkeypatch):
    from somnium.cli.config import config_app

    somnium_home, _ = _patch_config(monkeypatch, tmp_path)
    # Remove the config file to get empty scope
    (somnium_home / "config.toml").unlink()
    result = runner.invoke(config_app, ["list", "--scope", "global"])
    assert result.exit_code == 0
    assert "no config" in result.output


def test_config_path_global(tmp_path, monkeypatch):
    from somnium.cli.config import config_app

    _patch_config(monkeypatch, tmp_path)
    result = runner.invoke(config_app, ["path"])
    assert result.exit_code == 0
    assert "config.toml" in result.output
