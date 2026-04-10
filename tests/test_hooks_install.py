"""Tests for somnium.hooks.install (settings.json editing)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from somnium.hooks import install as install_module


@pytest.fixture
def fake_settings(tmp_path: Path, monkeypatch):
    """Redirect SETTINGS_PATH to a tmp file and stub out the MCP CLI
    helpers so tests don't depend on `claude mcp add` working."""
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(install_module, "SETTINGS_PATH", settings_path)
    monkeypatch.setattr(
        install_module,
        "_install_mcp_server",
        lambda: "+ mcpServers.somnium -> somnium-mcp",
    )
    monkeypatch.setattr(
        install_module,
        "_uninstall_mcp_server",
        lambda: "- mcpServers.somnium",
    )
    # Stub slash command helpers so tests don't touch the real
    # ~/.claude/commands/somnium/ directory.
    monkeypatch.setattr(
        install_module,
        "_install_slash_commands",
        lambda dry_run=False: ["+ /somnium:dream", "+ /somnium:search", "+ /somnium:status"],
    )
    monkeypatch.setattr(
        install_module,
        "_uninstall_slash_commands",
        lambda: [],
    )
    return settings_path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_install_from_empty_file(fake_settings):
    actions = install_module.install_hooks()
    assert any("PostToolUse" in a for a in actions)
    assert any("mcpServers.somnium" in a for a in actions)
    data = _read(fake_settings)
    assert "hooks" in data
    assert "PostToolUse" in data["hooks"]
    assert "Stop" in data["hooks"]
    assert "UserPromptSubmit" in data["hooks"]
    # MCP server is registered via `claude mcp add` (out of band), not
    # in settings.json. The settings file should NOT have mcpServers.
    assert "mcpServers" not in data


def test_install_preserves_existing_hooks(fake_settings):
    fake_settings.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(ls)"]},
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": "my-own-hook"}
                            ]
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    install_module.install_hooks()
    data = _read(fake_settings)
    assert data["permissions"] == {"allow": ["Bash(ls)"]}
    stop_list = data["hooks"]["Stop"]
    # User's own hook must still be there, plus Somnium's
    assert len(stop_list) == 2
    user_hook = next(g for g in stop_list if not g.get("_somnium"))
    assert user_hook["hooks"][0]["command"] == "my-own-hook"
    somnium_hook = next(g for g in stop_list if g.get("_somnium"))
    # Hooks are registered with their absolute path for PATH-independence.
    assert somnium_hook["hooks"][0]["command"].endswith("somnium-hook-stop")


def test_install_idempotent(fake_settings):
    install_module.install_hooks()
    first = fake_settings.read_text(encoding="utf-8")
    install_module.install_hooks()
    second = fake_settings.read_text(encoding="utf-8")
    assert first == second  # byte-for-byte stable


def test_uninstall_removes_only_somnium_hooks(fake_settings):
    fake_settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        {
                            "hooks": [
                                {"type": "command", "command": "my-own-hook"}
                            ]
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    install_module.install_hooks()
    install_module.uninstall_hooks()
    data = _read(fake_settings)
    stop_list = data["hooks"]["Stop"]
    assert len(stop_list) == 1
    assert stop_list[0]["hooks"][0]["command"] == "my-own-hook"


def test_uninstall_removes_empty_event_keys(fake_settings):
    install_module.install_hooks()
    install_module.uninstall_hooks()
    data = _read(fake_settings)
    # Since we only added Somnium hooks, the entire hooks section should
    # be gone after uninstall.
    assert "hooks" not in data
    assert "mcpServers" not in data


def test_install_strips_legacy_mcpservers_entry(fake_settings):
    """Older versions wrote mcpServers.somnium to settings.json. The
    new install path should clean that up while preserving other MCP
    entries that may have been put there manually."""
    fake_settings.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other": {"command": "other-mcp"},
                    "somnium": {"command": "somnium-mcp"},
                }
            }
        ),
        encoding="utf-8",
    )
    install_module.install_hooks()
    data = _read(fake_settings)
    assert "somnium" not in data.get("mcpServers", {})
    assert data["mcpServers"]["other"] == {"command": "other-mcp"}


def test_uninstall_noop_on_missing_file(fake_settings, monkeypatch):
    # fake_settings doesn't exist yet AND no MCP server is registered.
    monkeypatch.setattr(install_module, "_uninstall_mcp_server", lambda: None)
    actions = install_module.uninstall_hooks()
    assert actions == []
