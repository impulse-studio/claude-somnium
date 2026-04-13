"""Tests for somnium/cost.py and the CLI cost/config commands.

Covers:
  - voyage_cost computation (known model + fallback)
  - set_project / get_project context variable
  - _cost_path with and without SOMNIUM_HOME
  - log_cost writes valid JSONL (and is best-effort on error)
  - read_costs with source / since filters
  - summarize_costs aggregation
  - CLI `somnium costs` command via CliRunner
  - Config helpers: _resolve_key, _set_key, _toml_value, _is_secret_key, _serialize_toml
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from somnium.cli import app
from somnium.cli.config import (
    _is_secret_key,
    _resolve_key,
    _serialize_toml,
    _set_key,
    _toml_value,
)
from somnium.cost import (
    _cost_path,
    get_project,
    log_cost,
    read_costs,
    set_project,
    summarize_costs,
    voyage_cost,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def cost_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point SOMNIUM_HOME at a temp dir so cost log goes there."""
    somnium_home = tmp_path / "somnium"
    somnium_home.mkdir()
    monkeypatch.setenv("SOMNIUM_HOME", str(somnium_home))
    return somnium_home


@pytest.fixture
def cli_cost_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Full sandbox for CLI commands: HOME, SOMNIUM_HOME, embedder patches."""
    home = tmp_path / "home"
    home.mkdir()
    somnium_home = home / ".claude" / "somnium"
    somnium_home.mkdir(parents=True)

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SOMNIUM_HOME", str(somnium_home))
    monkeypatch.setenv("VOYAGE_API_KEY", "fake-key-not-used")

    from somnium.config import reset_config_cache

    reset_config_cache()

    return somnium_home


# ---------------------------------------------------------------------------
# voyage_cost
# ---------------------------------------------------------------------------


class TestVoyageCost:
    def test_known_model_voyage_3_5(self) -> None:
        # voyage-3.5 is $0.06 per 1M tokens
        cost = voyage_cost("voyage-3.5", 1_000_000)
        assert cost == pytest.approx(0.06)

    def test_known_model_voyage_code_3(self) -> None:
        # voyage-code-3 is $0.18 per 1M tokens
        cost = voyage_cost("voyage-code-3", 500_000)
        assert cost == pytest.approx(0.09)

    def test_known_model_voyage_3_5_lite(self) -> None:
        # voyage-3.5-lite is $0.02 per 1M tokens
        cost = voyage_cost("voyage-3.5-lite", 2_000_000)
        assert cost == pytest.approx(0.04)

    def test_unknown_model_falls_back_to_default(self) -> None:
        # Unknown model falls back to $0.06 per 1M tokens
        cost = voyage_cost("some-future-model", 1_000_000)
        assert cost == pytest.approx(0.06)

    def test_zero_tokens(self) -> None:
        cost = voyage_cost("voyage-3.5", 0)
        assert cost == 0.0

    def test_small_token_count(self) -> None:
        # 100 tokens at $0.06/1M
        cost = voyage_cost("voyage-3.5", 100)
        assert cost == pytest.approx(0.000006)


# ---------------------------------------------------------------------------
# set_project / get_project
# ---------------------------------------------------------------------------


class TestProjectContext:
    def test_default_project_is_global(self) -> None:
        assert get_project() == "global"

    def test_set_and_get_project(self) -> None:
        set_project("my-project")
        assert get_project() == "my-project"
        # Reset to avoid leaking into other tests
        set_project("global")

    def test_set_project_overwrites_previous(self) -> None:
        set_project("alpha")
        set_project("beta")
        assert get_project() == "beta"
        set_project("global")


# ---------------------------------------------------------------------------
# _cost_path
# ---------------------------------------------------------------------------


class TestCostPath:
    def test_with_somnium_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom_home = tmp_path / "custom_somnium"
        monkeypatch.setenv("SOMNIUM_HOME", str(custom_home))
        result = _cost_path()
        assert result == custom_home / "costs.jsonl"

    def test_without_somnium_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SOMNIUM_HOME", raising=False)
        result = _cost_path()
        assert result == Path.home() / ".claude" / "somnium" / "costs.jsonl"


# ---------------------------------------------------------------------------
# log_cost
# ---------------------------------------------------------------------------


class TestLogCost:
    def test_writes_valid_jsonl(self, cost_sandbox: Path) -> None:
        log_cost(
            source="index",
            model="voyage-3.5",
            tokens=1000,
            cost_usd=0.00006,
        )
        log_path = cost_sandbox / "costs.jsonl"
        assert log_path.exists()

        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["source"] == "index"
        assert entry["model"] == "voyage-3.5"
        assert entry["tokens"] == 1000
        assert entry["cost_usd"] == pytest.approx(0.00006)
        assert "ts" in entry

    def test_uses_get_project_when_project_not_given(
        self, cost_sandbox: Path
    ) -> None:
        set_project("test-proj")
        log_cost(source="index", model="voyage-3.5", tokens=100, cost_usd=0.001)
        set_project("global")

        log_path = cost_sandbox / "costs.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["project"] == "test-proj"

    def test_explicit_project_overrides_context(self, cost_sandbox: Path) -> None:
        set_project("context-proj")
        log_cost(
            source="dream",
            model="claude-haiku-4-5",
            tokens=500,
            cost_usd=0.01,
            project="explicit-proj",
        )
        set_project("global")

        log_path = cost_sandbox / "costs.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["project"] == "explicit-proj"

    def test_context_field_stored(self, cost_sandbox: Path) -> None:
        log_cost(
            source="index",
            model="voyage-3.5",
            tokens=100,
            cost_usd=0.001,
            context="embedding batch 42",
        )
        log_path = cost_sandbox / "costs.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["context"] == "embedding batch 42"

    def test_extra_fields_merged(self, cost_sandbox: Path) -> None:
        log_cost(
            source="dream",
            model="claude-haiku-4-5",
            tokens=200,
            cost_usd=0.005,
            extra={"session_id": "abc123"},
        )
        log_path = cost_sandbox / "costs.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["session_id"] == "abc123"

    def test_multiple_entries_appended(self, cost_sandbox: Path) -> None:
        for i in range(3):
            log_cost(
                source="index",
                model="voyage-3.5",
                tokens=100 * (i + 1),
                cost_usd=0.001 * (i + 1),
            )
        log_path = cost_sandbox / "costs.jsonl"
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    def test_cost_usd_rounded(self, cost_sandbox: Path) -> None:
        log_cost(
            source="index",
            model="voyage-3.5",
            tokens=100,
            cost_usd=0.123456789,
        )
        log_path = cost_sandbox / "costs.jsonl"
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        # Rounded to 6 decimal places
        assert entry["cost_usd"] == pytest.approx(0.123457)

    def test_best_effort_no_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """log_cost swallows exceptions silently."""
        # Point to a path that cannot be written (a file masquerading as dir)
        bogus = tmp_path / "bogus"
        bogus.write_text("not a directory")
        monkeypatch.setenv("SOMNIUM_HOME", str(bogus))
        # Should not raise
        log_cost(source="index", model="voyage-3.5", tokens=100, cost_usd=0.001)

    def test_creates_parent_directories(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        nested = tmp_path / "deep" / "nested" / "somnium"
        monkeypatch.setenv("SOMNIUM_HOME", str(nested))
        log_cost(source="index", model="voyage-3.5", tokens=100, cost_usd=0.001)
        assert (nested / "costs.jsonl").exists()


# ---------------------------------------------------------------------------
# read_costs
# ---------------------------------------------------------------------------


def _write_entries(cost_sandbox: Path, entries: list[dict]) -> None:
    """Helper to write pre-built entries directly to the cost log."""
    log_path = cost_sandbox / "costs.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


class TestReadCosts:
    def test_empty_when_no_file(self, cost_sandbox: Path) -> None:
        entries = read_costs()
        assert entries == []

    def test_reads_all_entries(self, cost_sandbox: Path) -> None:
        _write_entries(
            cost_sandbox,
            [
                {"ts": "2026-04-10T12:00:00+00:00", "source": "index", "tokens": 100, "cost_usd": 0.001, "model": "voyage-3.5", "project": "global", "context": ""},
                {"ts": "2026-04-10T13:00:00+00:00", "source": "dream", "tokens": 200, "cost_usd": 0.01, "model": "claude-haiku-4-5", "project": "global", "context": ""},
            ],
        )
        entries = read_costs()
        assert len(entries) == 2

    def test_filter_by_source(self, cost_sandbox: Path) -> None:
        _write_entries(
            cost_sandbox,
            [
                {"ts": "2026-04-10T12:00:00+00:00", "source": "index", "tokens": 100, "cost_usd": 0.001, "model": "voyage-3.5", "project": "global", "context": ""},
                {"ts": "2026-04-10T13:00:00+00:00", "source": "dream", "tokens": 200, "cost_usd": 0.01, "model": "claude-haiku-4-5", "project": "global", "context": ""},
                {"ts": "2026-04-10T14:00:00+00:00", "source": "index", "tokens": 300, "cost_usd": 0.002, "model": "voyage-3.5", "project": "global", "context": ""},
            ],
        )
        entries = read_costs(source="index")
        assert len(entries) == 2
        assert all(e["source"] == "index" for e in entries)

    def test_filter_by_source_no_match(self, cost_sandbox: Path) -> None:
        _write_entries(
            cost_sandbox,
            [
                {"ts": "2026-04-10T12:00:00+00:00", "source": "index", "tokens": 100, "cost_usd": 0.001, "model": "voyage-3.5", "project": "global", "context": ""},
            ],
        )
        entries = read_costs(source="dream")
        assert entries == []

    def test_filter_by_since(self, cost_sandbox: Path) -> None:
        _write_entries(
            cost_sandbox,
            [
                {"ts": "2026-04-08T12:00:00+00:00", "source": "index", "tokens": 100, "cost_usd": 0.001, "model": "voyage-3.5", "project": "global", "context": ""},
                {"ts": "2026-04-10T12:00:00+00:00", "source": "index", "tokens": 200, "cost_usd": 0.002, "model": "voyage-3.5", "project": "global", "context": ""},
                {"ts": "2026-04-12T12:00:00+00:00", "source": "dream", "tokens": 300, "cost_usd": 0.01, "model": "claude-haiku-4-5", "project": "global", "context": ""},
            ],
        )
        since = dt.datetime(2026, 4, 10, 0, 0, 0, tzinfo=dt.UTC)
        entries = read_costs(since=since)
        assert len(entries) == 2
        assert entries[0]["tokens"] == 200
        assert entries[1]["tokens"] == 300

    def test_filter_by_since_and_source(self, cost_sandbox: Path) -> None:
        _write_entries(
            cost_sandbox,
            [
                {"ts": "2026-04-08T12:00:00+00:00", "source": "index", "tokens": 100, "cost_usd": 0.001, "model": "voyage-3.5", "project": "global", "context": ""},
                {"ts": "2026-04-10T12:00:00+00:00", "source": "index", "tokens": 200, "cost_usd": 0.002, "model": "voyage-3.5", "project": "global", "context": ""},
                {"ts": "2026-04-12T12:00:00+00:00", "source": "dream", "tokens": 300, "cost_usd": 0.01, "model": "claude-haiku-4-5", "project": "global", "context": ""},
            ],
        )
        since = dt.datetime(2026, 4, 9, 0, 0, 0, tzinfo=dt.UTC)
        entries = read_costs(since=since, source="index")
        assert len(entries) == 1
        assert entries[0]["tokens"] == 200

    def test_skips_blank_lines(self, cost_sandbox: Path) -> None:
        log_path = cost_sandbox / "costs.jsonl"
        log_path.write_text(
            json.dumps({"ts": "2026-04-10T12:00:00+00:00", "source": "index", "tokens": 100, "cost_usd": 0.001, "model": "v", "project": "g", "context": ""})
            + "\n\n\n"
            + json.dumps({"ts": "2026-04-10T13:00:00+00:00", "source": "dream", "tokens": 200, "cost_usd": 0.01, "model": "c", "project": "g", "context": ""})
            + "\n",
            encoding="utf-8",
        )
        entries = read_costs()
        assert len(entries) == 2

    def test_skips_malformed_json(self, cost_sandbox: Path) -> None:
        log_path = cost_sandbox / "costs.jsonl"
        log_path.write_text(
            json.dumps({"ts": "2026-04-10T12:00:00+00:00", "source": "index", "tokens": 100, "cost_usd": 0.001, "model": "v", "project": "g", "context": ""})
            + "\nNOT VALID JSON\n"
            + json.dumps({"ts": "2026-04-10T13:00:00+00:00", "source": "dream", "tokens": 200, "cost_usd": 0.01, "model": "c", "project": "g", "context": ""})
            + "\n",
            encoding="utf-8",
        )
        entries = read_costs()
        assert len(entries) == 2

    def test_since_filter_skips_entries_with_invalid_ts(
        self, cost_sandbox: Path
    ) -> None:
        _write_entries(
            cost_sandbox,
            [
                {"ts": "not-a-timestamp", "source": "index", "tokens": 100, "cost_usd": 0.001, "model": "v", "project": "g", "context": ""},
                {"ts": "2026-04-12T12:00:00+00:00", "source": "index", "tokens": 200, "cost_usd": 0.002, "model": "v", "project": "g", "context": ""},
            ],
        )
        since = dt.datetime(2026, 4, 10, 0, 0, 0, tzinfo=dt.UTC)
        entries = read_costs(since=since)
        # The entry with the invalid timestamp is skipped when since is set
        assert len(entries) == 1
        assert entries[0]["tokens"] == 200


# ---------------------------------------------------------------------------
# summarize_costs
# ---------------------------------------------------------------------------


class TestSummarizeCosts:
    def test_empty_entries(self) -> None:
        summary = summarize_costs([])
        assert summary == {
            "total_usd": 0.0,
            "total_tokens": 0,
            "count": 0,
            "by_source": {},
            "by_model": {},
        }

    def test_single_entry(self) -> None:
        entries = [
            {"source": "index", "model": "voyage-3.5", "tokens": 1000, "cost_usd": 0.06},
        ]
        summary = summarize_costs(entries)
        assert summary["total_usd"] == pytest.approx(0.06)
        assert summary["total_tokens"] == 1000
        assert summary["count"] == 1
        assert summary["by_source"] == {"index": pytest.approx(0.06)}
        assert summary["by_model"] == {"voyage-3.5": pytest.approx(0.06)}

    def test_multiple_entries_aggregation(self) -> None:
        entries = [
            {"source": "index", "model": "voyage-3.5", "tokens": 1000, "cost_usd": 0.06},
            {"source": "index", "model": "voyage-code-3", "tokens": 2000, "cost_usd": 0.36},
            {"source": "dream", "model": "claude-haiku-4-5", "tokens": 500, "cost_usd": 0.01},
        ]
        summary = summarize_costs(entries)
        assert summary["total_usd"] == pytest.approx(0.43)
        assert summary["total_tokens"] == 3500
        assert summary["count"] == 3
        assert summary["by_source"]["index"] == pytest.approx(0.42)
        assert summary["by_source"]["dream"] == pytest.approx(0.01)
        assert summary["by_model"]["voyage-3.5"] == pytest.approx(0.06)
        assert summary["by_model"]["voyage-code-3"] == pytest.approx(0.36)
        assert summary["by_model"]["claude-haiku-4-5"] == pytest.approx(0.01)

    def test_missing_fields_default_gracefully(self) -> None:
        entries = [
            {"something": "else"},
            {"source": "index", "tokens": 100},
        ]
        summary = summarize_costs(entries)
        assert summary["count"] == 2
        assert summary["total_tokens"] == 100
        # Missing cost_usd defaults to 0.0
        assert summary["total_usd"] == pytest.approx(0.0)
        # Missing source/model become "unknown"
        assert "unknown" in summary["by_source"]

    def test_by_source_and_by_model_sorted(self) -> None:
        entries = [
            {"source": "dream", "model": "z-model", "tokens": 10, "cost_usd": 0.1},
            {"source": "alpha", "model": "a-model", "tokens": 20, "cost_usd": 0.2},
        ]
        summary = summarize_costs(entries)
        assert list(summary["by_source"].keys()) == ["alpha", "dream"]
        assert list(summary["by_model"].keys()) == ["a-model", "z-model"]

    def test_rounding(self) -> None:
        entries = [
            {"source": "index", "model": "v", "tokens": 1, "cost_usd": 0.00001},
            {"source": "index", "model": "v", "tokens": 1, "cost_usd": 0.00002},
            {"source": "index", "model": "v", "tokens": 1, "cost_usd": 0.00003},
        ]
        summary = summarize_costs(entries)
        assert summary["total_usd"] == pytest.approx(0.0001)
        assert summary["by_source"]["index"] == pytest.approx(0.0001)


# ---------------------------------------------------------------------------
# CLI: somnium costs
# ---------------------------------------------------------------------------


class TestCostsCLI:
    def test_no_data_message(
        self, runner: CliRunner, cli_cost_sandbox: Path
    ) -> None:
        result = runner.invoke(app, ["costs"])
        assert result.exit_code == 0
        assert "No cost data" in result.stdout

    def test_shows_entries(
        self, runner: CliRunner, cli_cost_sandbox: Path
    ) -> None:
        _write_entries(
            cli_cost_sandbox,
            [
                {
                    "ts": dt.datetime.now(tz=dt.UTC).isoformat(),
                    "source": "index",
                    "model": "voyage-3.5",
                    "tokens": 5000,
                    "cost_usd": 0.0003,
                    "project": "global",
                    "context": "",
                },
            ],
        )
        result = runner.invoke(app, ["costs", "--last", "all"])
        assert result.exit_code == 0
        assert "voyage-3.5" in result.stdout

    def test_invalid_period(
        self, runner: CliRunner, cli_cost_sandbox: Path
    ) -> None:
        result = runner.invoke(app, ["costs", "--last", "999x"])
        assert result.exit_code == 1
        assert "Unknown period" in result.stdout

    def test_source_filter(
        self, runner: CliRunner, cli_cost_sandbox: Path
    ) -> None:
        _write_entries(
            cli_cost_sandbox,
            [
                {
                    "ts": dt.datetime.now(tz=dt.UTC).isoformat(),
                    "source": "index",
                    "model": "voyage-3.5",
                    "tokens": 1000,
                    "cost_usd": 0.001,
                    "project": "global",
                    "context": "",
                },
                {
                    "ts": dt.datetime.now(tz=dt.UTC).isoformat(),
                    "source": "dream",
                    "model": "claude-haiku-4-5",
                    "tokens": 2000,
                    "cost_usd": 0.01,
                    "project": "global",
                    "context": "",
                },
            ],
        )
        result = runner.invoke(app, ["costs", "--last", "all", "--source", "dream"])
        assert result.exit_code == 0
        assert "dream" in result.stdout
        # The index entry should not appear
        assert "voyage-3.5" not in result.stdout


# ---------------------------------------------------------------------------
# Config helpers: _resolve_key
# ---------------------------------------------------------------------------


class TestResolveKey:
    def test_top_level_key(self) -> None:
        data = {"name": "somnium", "version": 1}
        assert _resolve_key(data, "name") == "somnium"

    def test_nested_key(self) -> None:
        data = {"dream": {"model": "claude-haiku-4-5", "enabled": True}}
        assert _resolve_key(data, "dream.model") == "claude-haiku-4-5"

    def test_deeply_nested_key(self) -> None:
        data = {"a": {"b": {"c": {"d": 42}}}}
        assert _resolve_key(data, "a.b.c.d") == 42

    def test_missing_key_returns_none(self) -> None:
        data = {"name": "somnium"}
        assert _resolve_key(data, "missing") is None

    def test_missing_nested_key_returns_none(self) -> None:
        data = {"dream": {"model": "claude-haiku-4-5"}}
        assert _resolve_key(data, "dream.nonexistent") is None

    def test_missing_intermediate_key_returns_none(self) -> None:
        data = {"dream": {"model": "claude-haiku-4-5"}}
        assert _resolve_key(data, "nope.model") is None

    def test_returns_dict_for_section(self) -> None:
        data = {"dream": {"model": "claude-haiku-4-5", "enabled": True}}
        result = _resolve_key(data, "dream")
        assert isinstance(result, dict)
        assert result["model"] == "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Config helpers: _set_key
# ---------------------------------------------------------------------------


class TestSetKey:
    def test_set_top_level(self) -> None:
        data: dict = {}
        _set_key(data, "name", "somnium")
        assert data["name"] == "somnium"

    def test_set_nested_creates_intermediates(self) -> None:
        data: dict = {}
        _set_key(data, "dream.model", "claude-haiku-4-5")
        assert data["dream"]["model"] == "claude-haiku-4-5"

    def test_set_deeply_nested(self) -> None:
        data: dict = {}
        _set_key(data, "a.b.c.d", "deep")
        assert data["a"]["b"]["c"]["d"] == "deep"

    def test_overwrites_existing_value(self) -> None:
        data = {"dream": {"model": "old-model"}}
        _set_key(data, "dream.model", "new-model")
        assert data["dream"]["model"] == "new-model"

    def test_parses_int(self) -> None:
        data: dict = {}
        _set_key(data, "top_k", "10")
        assert data["top_k"] == 10
        assert isinstance(data["top_k"], int)

    def test_parses_float(self) -> None:
        data: dict = {}
        _set_key(data, "threshold", "0.75")
        assert data["threshold"] == pytest.approx(0.75)
        assert isinstance(data["threshold"], float)

    def test_parses_bool_true(self) -> None:
        data: dict = {}
        _set_key(data, "enabled", "true")
        assert data["enabled"] is True

    def test_parses_bool_false(self) -> None:
        data: dict = {}
        _set_key(data, "enabled", "false")
        assert data["enabled"] is False

    def test_string_value_stays_string(self) -> None:
        data: dict = {}
        _set_key(data, "model", "claude-haiku-4-5")
        assert data["model"] == "claude-haiku-4-5"
        assert isinstance(data["model"], str)

    def test_returns_data(self) -> None:
        data: dict = {}
        result = _set_key(data, "key", "val")
        assert result is data


# ---------------------------------------------------------------------------
# Config helpers: _toml_value
# ---------------------------------------------------------------------------


class TestTomlValue:
    def test_string(self) -> None:
        assert _toml_value("hello") == '"hello"'

    def test_int(self) -> None:
        assert _toml_value(42) == "42"

    def test_float(self) -> None:
        assert _toml_value(3.14) == "3.14"

    def test_bool_true(self) -> None:
        assert _toml_value(True) == "true"

    def test_bool_false(self) -> None:
        assert _toml_value(False) == "false"

    def test_list_of_strings(self) -> None:
        result = _toml_value(["a", "b", "c"])
        assert result == '["a", "b", "c"]'

    def test_list_of_ints(self) -> None:
        result = _toml_value([1, 2, 3])
        assert result == "[1, 2, 3]"

    def test_empty_list(self) -> None:
        assert _toml_value([]) == "[]"


# ---------------------------------------------------------------------------
# Config helpers: _is_secret_key
# ---------------------------------------------------------------------------


class TestIsSecretKey:
    def test_api_key_detected(self) -> None:
        assert _is_secret_key("api_key") is True
        assert _is_secret_key("voyage_api_key") is True
        assert _is_secret_key("embeddings.api_key") is True

    def test_token_detected(self) -> None:
        assert _is_secret_key("auth_token") is True
        assert _is_secret_key("oauth_token") is True

    def test_secret_detected(self) -> None:
        assert _is_secret_key("client_secret") is True

    def test_normal_key_not_secret(self) -> None:
        assert _is_secret_key("model") is False
        assert _is_secret_key("dream.enabled") is False
        assert _is_secret_key("top_k") is False

    def test_case_insensitive(self) -> None:
        assert _is_secret_key("API_KEY") is True
        assert _is_secret_key("Api_Key") is True


# ---------------------------------------------------------------------------
# Config helpers: _serialize_toml
# ---------------------------------------------------------------------------


class TestSerializeToml:
    def test_simple_key_values(self) -> None:
        data = {"name": "somnium", "version": 1}
        lines: list[str] = []
        _serialize_toml(data, lines, prefix="")
        text = "\n".join(lines)
        assert 'name = "somnium"' in text
        assert "version = 1" in text

    def test_nested_sections(self) -> None:
        data = {"dream": {"model": "claude-haiku-4-5", "enabled": True}}
        lines: list[str] = []
        _serialize_toml(data, lines, prefix="")
        text = "\n".join(lines)
        assert "[dream]" in text
        assert 'model = "claude-haiku-4-5"' in text
        assert "enabled = true" in text

    def test_deeply_nested(self) -> None:
        data = {"a": {"b": {"value": 42}}}
        lines: list[str] = []
        _serialize_toml(data, lines, prefix="")
        text = "\n".join(lines)
        assert "[a]" in text or "[a.b]" in text
        assert "value = 42" in text

    def test_mixed_scalars_and_sections(self) -> None:
        data = {"top": "val", "section": {"key": "inner"}}
        lines: list[str] = []
        _serialize_toml(data, lines, prefix="")
        text = "\n".join(lines)
        assert 'top = "val"' in text
        assert "[section]" in text
        assert 'key = "inner"' in text
