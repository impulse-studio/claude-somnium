"""Tests for somnium.hooks.install (settings.json editing)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from somnium.hooks import install as install_module


@pytest.fixture
def fake_settings(tmp_path: Path, monkeypatch):
    """Redirect SETTINGS_PATH to a tmp file."""
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(install_module, "SETTINGS_PATH", settings_path)
    return settings_path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_install_from_empty_file(fake_settings):
    actions = install_module.install_hooks()
    assert any("PostToolUse" in a for a in actions)
    data = _read(fake_settings)
    assert "hooks" in data
    assert "PostToolUse" in data["hooks"]
    assert "Stop" in data["hooks"]
    assert "UserPromptSubmit" in data["hooks"]
    # MCP server must also be registered
    assert "mcpServers" in data
    assert data["mcpServers"]["somnium"] == {"command": "somnium-mcp"}


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
    assert somnium_hook["hooks"][0]["command"] == "somnium-hook-stop"


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


def test_uninstall_preserves_other_mcp_servers(fake_settings):
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
    install_module.uninstall_hooks()
    data = _read(fake_settings)
    assert "mcpServers" in data
    assert "somnium" not in data["mcpServers"]
    assert data["mcpServers"]["other"] == {"command": "other-mcp"}


def test_uninstall_noop_on_missing_file(fake_settings):
    # fake_settings doesn't exist yet
    actions = install_module.uninstall_hooks()
    assert actions == []
