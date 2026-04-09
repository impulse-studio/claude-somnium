"""Install / uninstall Somnium hooks and MCP server in ~/.claude/settings.json.

We edit settings.json idempotently:
  - Register a PostToolUse matcher for Write|Edit|MultiEdit|NotebookEdit
    pointing at `somnium-hook-post-tool-use`.
  - Register a Stop hook pointing at `somnium-hook-stop`.
  - Register a UserPromptSubmit hook pointing at `somnium-hook-user-prompt-submit`.
  - Register the `somnium` MCP server pointing at `somnium-mcp`.

Each hook entry is tagged with a `_somnium` marker so we can find and
remove them on uninstall without touching user-maintained hooks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
SOMNIUM_MARKER = "_somnium"
SOMNIUM_MCP_NAME = "somnium"
SOMNIUM_MCP_COMMAND = "somnium-mcp"


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

        new_group = {
            SOMNIUM_MARKER: True,
            "hooks": [
                {
                    "type": "command",
                    "command": spec.command,
                    "timeout": spec.timeout,
                }
            ],
        }
        if spec.matcher is not None:
            new_group["matcher"] = spec.matcher

        if existing is None:
            event_list.append(new_group)
            actions.append(f"+ {spec.event} ({spec.matcher or '*'}) -> {spec.command}")
        else:
            # Update in place if the inner command differs.
            if existing != new_group:
                existing.clear()
                existing.update(new_group)
                actions.append(
                    f"~ {spec.event} ({spec.matcher or '*'}) -> {spec.command}"
                )
            else:
                actions.append(
                    f"= {spec.event} ({spec.matcher or '*'}) already installed"
                )

    # ----- MCP server ---------------------------------------------
    mcp_section = settings.setdefault("mcpServers", {})
    desired_entry = {"command": SOMNIUM_MCP_COMMAND}
    existing_entry = mcp_section.get(SOMNIUM_MCP_NAME)
    if existing_entry == desired_entry:
        actions.append(f"= mcpServers.{SOMNIUM_MCP_NAME} already registered")
    else:
        mcp_section[SOMNIUM_MCP_NAME] = desired_entry
        verb = "+" if existing_entry is None else "~"
        actions.append(
            f"{verb} mcpServers.{SOMNIUM_MCP_NAME} -> {SOMNIUM_MCP_COMMAND}"
        )

    if not dry_run:
        _save_settings(settings)
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

    # Unregister MCP server
    mcp_section = settings.get("mcpServers", {})
    if isinstance(mcp_section, dict) and SOMNIUM_MCP_NAME in mcp_section:
        del mcp_section[SOMNIUM_MCP_NAME]
        actions.append(f"- mcpServers.{SOMNIUM_MCP_NAME}")
        if not mcp_section:
            settings.pop("mcpServers", None)

    if not dry_run:
        _save_settings(settings)
    return actions
