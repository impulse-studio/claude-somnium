"""Install / uninstall Somnium hooks and MCP server.

Two distinct edits:

1. **Hooks** are written to `~/.claude/settings.json` under
   `hooks.{PostToolUse,Stop,UserPromptSubmit}`. Each Somnium-managed
   group is tagged with `_somnium: true` so uninstall can find them
   without touching user-maintained hooks.

2. **The MCP server** is registered via the canonical
   `claude mcp add` CLI (which writes to `~/.claude.json` under user
   scope). We do NOT edit that file directly because Claude Code may
   change its schema, and the CLI handles edge cases like health
   checks and scope semantics.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
SOMNIUM_MARKER = "_somnium"
SOMNIUM_MCP_NAME = "somnium"
SOMNIUM_MCP_COMMAND = "somnium-mcp"


def _resolve_bin(name: str) -> str:
    """Return the absolute path to a Somnium CLI binary.

    Claude Code launches hooks and MCP servers from a clean shell that
    typically does NOT include the Python venv on PATH, so we always
    register the absolute path of the binary that ships next to the
    current interpreter.
    """
    resolved = shutil.which(name)
    if resolved:
        return resolved
    import sys

    candidate = Path(sys.executable).parent / name
    if candidate.exists():
        return str(candidate)
    return name  # last-resort: hope it's on PATH at runtime


@dataclass
class HookSpec:
    event: str
    matcher: str | None
    command: str
    timeout: int = 10


DEFAULT_HOOKS: list[HookSpec] = [
    HookSpec(
        event="PostToolUse",
        matcher="Write|Edit|MultiEdit|NotebookEdit",
        command="somnium-hook-post-tool-use",
        timeout=10,
    ),
    HookSpec(
        event="Stop",
        matcher=None,
        command="somnium-hook-stop",
        timeout=5,
    ),
    HookSpec(
        event="UserPromptSubmit",
        matcher=None,
        command="somnium-hook-user-prompt-submit",
        timeout=5,
    ),
]


def _load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Cannot parse {SETTINGS_PATH}: {exc}. "
            "Fix the JSON manually before running `somnium init`."
        ) from exc


def _save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(settings, indent=2) + "\n", encoding="utf-8"
    )


def _is_somnium_hook(hook_group: dict) -> bool:
    """True if this hook group was created by Somnium."""
    if hook_group.get(SOMNIUM_MARKER) is True:
        return True
    # Fallback: check if any inner command mentions somnium-hook-
    for h in hook_group.get("hooks", []) or []:
        cmd = h.get("command", "") if isinstance(h, dict) else ""
        if "somnium-hook-" in cmd:
            return True
    return False


def _claude_cli_available() -> bool:
    return shutil.which("claude") is not None


def _mcp_server_present() -> bool:
    """Best-effort check whether the somnium MCP server is already
    registered at user scope. Falls back to False on any error."""
    try:
        result = subprocess.run(
            ["claude", "mcp", "get", SOMNIUM_MCP_NAME],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _resolve_mcp_command() -> str:
    return _resolve_bin(SOMNIUM_MCP_COMMAND)


def _install_mcp_server() -> str:
    """Register the somnium MCP server via the canonical CLI."""
    if not _claude_cli_available():
        return "x mcpServers.somnium SKIPPED (claude CLI not on PATH)"

    if _mcp_server_present():
        return f"= mcpServers.{SOMNIUM_MCP_NAME} already registered"

    command_path = _resolve_mcp_command()

    try:
        proc = subprocess.run(
            [
                "claude",
                "mcp",
                "add",
                "--scope",
                "user",
                SOMNIUM_MCP_NAME,
                command_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            return f"+ mcpServers.{SOMNIUM_MCP_NAME} -> {command_path}"
        return (
            f"x mcpServers.{SOMNIUM_MCP_NAME} FAILED: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"x mcpServers.{SOMNIUM_MCP_NAME} FAILED: {exc}"


def _uninstall_mcp_server() -> str | None:
    """Unregister via CLI. Returns an action string or None if nothing
    needed to be done."""
    if not _claude_cli_available():
        return None
    if not _mcp_server_present():
        return None
    try:
        proc = subprocess.run(
            ["claude", "mcp", "remove", "--scope", "user", SOMNIUM_MCP_NAME],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode == 0:
            return f"- mcpServers.{SOMNIUM_MCP_NAME}"
        return (
            f"x mcpServers.{SOMNIUM_MCP_NAME} REMOVE FAILED: "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )
    except Exception as exc:  # noqa: BLE001
        return f"x mcpServers.{SOMNIUM_MCP_NAME} REMOVE FAILED: {exc}"


def install_hooks(dry_run: bool = False) -> list[str]:
    """Install all Somnium hooks AND register the Somnium MCP server.
    Returns a list of human-readable actions that were (or would be) taken."""
    settings = _load_settings()
    actions: list[str] = []

    # ----- Hooks --------------------------------------------------
    hooks_section = settings.setdefault("hooks", {})

    for spec in DEFAULT_HOOKS:
        event_list = hooks_section.setdefault(spec.event, [])

        # Check whether we already have a Somnium entry for this spec.
        existing = None
        for group in event_list:
            if not isinstance(group, dict):
                continue
            if not _is_somnium_hook(group):
                continue
            if group.get("matcher") != spec.matcher:
                continue
            existing = group
            break

        absolute_command = _resolve_bin(spec.command)
        new_group = {
            SOMNIUM_MARKER: True,
            "hooks": [
                {
                    "type": "command",
                    "command": absolute_command,
                    "timeout": spec.timeout,
                }
            ],
        }
        if spec.matcher is not None:
            new_group["matcher"] = spec.matcher

        if existing is None:
            event_list.append(new_group)
            actions.append(
                f"+ {spec.event} ({spec.matcher or '*'}) -> {absolute_command}"
            )
        else:
            # Update in place if the inner command differs.
            if existing != new_group:
                existing.clear()
                existing.update(new_group)
                actions.append(
                    f"~ {spec.event} ({spec.matcher or '*'}) -> {absolute_command}"
                )
            else:
                actions.append(
                    f"= {spec.event} ({spec.matcher or '*'}) already installed"
                )

    # Strip any leftover legacy mcpServers entry from settings.json —
    # earlier versions wrote it there. Canonical location is ~/.claude.json.
    if isinstance(settings.get("mcpServers"), dict):
        if SOMNIUM_MCP_NAME in settings["mcpServers"]:
            del settings["mcpServers"][SOMNIUM_MCP_NAME]
        if not settings["mcpServers"]:
            del settings["mcpServers"]

    if not dry_run:
        _save_settings(settings)

    # ----- MCP server (via canonical CLI) -------------------------
    if not dry_run:
        actions.append(_install_mcp_server())
    else:
        actions.append(f"+ mcpServers.{SOMNIUM_MCP_NAME} (would call `claude mcp add`)")

    return actions


def uninstall_hooks(dry_run: bool = False) -> list[str]:
    """Remove all Somnium-marked hooks AND unregister the MCP server."""
    settings = _load_settings()
    actions: list[str] = []
    hooks_section = settings.get("hooks", {})

    if hooks_section:
        for event, event_list in list(hooks_section.items()):
            if not isinstance(event_list, list):
                continue
            kept: list = []
            for group in event_list:
                if isinstance(group, dict) and _is_somnium_hook(group):
                    actions.append(
                        f"- {event} ({group.get('matcher', '*')})"
                    )
                    continue
                kept.append(group)
            if kept:
                hooks_section[event] = kept
            else:
                del hooks_section[event]

        if not hooks_section:
            settings.pop("hooks", None)

    # Strip any legacy mcpServers entry from settings.json
    mcp_section = settings.get("mcpServers", {})
    if isinstance(mcp_section, dict) and SOMNIUM_MCP_NAME in mcp_section:
        del mcp_section[SOMNIUM_MCP_NAME]
        if not mcp_section:
            settings.pop("mcpServers", None)

    if not dry_run:
        _save_settings(settings)
        # Canonical MCP unregister via CLI
        action = _uninstall_mcp_server()
        if action:
            actions.append(action)
    return actions
