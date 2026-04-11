"""``somnium status`` command and statusline install/uninstall."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.table import Table

from ..config import get_config, reset_config_cache
from ..storage.parquet_store import ParquetStore
from . import app, console

if TYPE_CHECKING:
    from ..config import SomniumConfig


@app.command()
def status(
    install_line: bool = typer.Option(
        False, "--install-line", help="Install the Somnium status line in Claude Code."
    ),
    uninstall_line: bool = typer.Option(
        False, "--uninstall-line", help="Remove the Somnium status line from Claude Code."
    ),
) -> None:
    """Show a full health snapshot: indexes, hooks, MCP server, config."""
    if install_line:
        _install_statusline()
        return
    if uninstall_line:
        _uninstall_statusline()
        return

    reset_config_cache()
    cfg = get_config()

    _print_memory_indexes(cfg)
    _print_code_index(cfg)
    _print_hooks_status()
    _print_mcp_status()
    _print_config_status(cfg)
    _print_statusline_tip()


# --- Index tables --------------------------------------------------------


def _index_row(
    label: str, path: Path | None, missing_msg: str = "(not built)"
) -> tuple[str, str, str, str, str]:
    if path is None:
        return (label, missing_msg, "-", "-", "-")
    if not path.exists():
        return (label, str(path), "-", "-", "-")
    with ParquetStore(path) as store:
        s = store.stats()
        return (label, str(path), str(s["files"]), str(s["chunks"]), str(s["embedding_dim"]))


def _print_memory_indexes(cfg: SomniumConfig) -> None:
    console.rule("[bold]Memory indexes[/]")
    table = Table(show_header=True, header_style="bold")
    table.add_column("Scope")
    table.add_column("Index path", overflow="fold")
    table.add_column("Files", justify="right")
    table.add_column("Chunks", justify="right")
    table.add_column("Dim", justify="right")

    table.add_row(*_index_row("global", cfg.global_index_path))
    if cfg.project_index_path:
        table.add_row(*_index_row("project", cfg.project_index_path))
    else:
        table.add_row("project", "(no project detected)", "-", "-", "-")

    console.print(table)
    console.print(
        "[dim]Updated by: PostToolUse hook (on Write/Edit) + dream agent "
        "(after Stop) + manual `somnium index`.[/]"
    )


def _print_code_index(cfg: SomniumConfig) -> None:
    console.rule("[bold]Code index[/]")
    if cfg.project_code_index_path is None:
        console.print("[dim](no project detected — no code index)[/]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Index path", overflow="fold")
    table.add_column("Files", justify="right")
    table.add_column("Chunks", justify="right")

    if not cfg.project_code_index_path.exists():
        table.add_row(str(cfg.project_code_index_path), "[dim]not built[/]", "[dim]not built[/]")
        console.print(table)
        console.print("[yellow]Run [cyan]somnium index --code[/] in this repo to build it.[/]")
        return

    with ParquetStore(cfg.project_code_index_path) as store:
        s = store.stats()
        table.add_row(str(cfg.project_code_index_path), str(s["files"]), str(s["chunks"]))
    console.print(table)
    console.print(
        "[dim]Updated by: PostToolUse hook (incremental, when Claude edits "
        "a source file) + manual `somnium index --code` (full rebuild).[/]"
    )


# --- Hooks status --------------------------------------------------------


def _read_somnium_hooks_from_settings() -> dict[str, str]:
    settings_path = Path.home() / ".claude" / "settings.json"
    if not settings_path.exists():
        return {}
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    result: dict[str, str] = {}
    for event, groups in (data.get("hooks") or {}).items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            if not group.get("_somnium"):
                continue
            inner = group.get("hooks") or []
            if inner and isinstance(inner[0], dict):
                cmd = inner[0].get("command")
                if isinstance(cmd, str):
                    result[event] = cmd
    return result


def _print_hooks_status() -> None:
    console.rule("[bold]Hooks[/]")
    expected = ["PostToolUse", "Stop", "UserPromptSubmit"]
    installed = _read_somnium_hooks_from_settings()

    if not installed:
        console.print("[red]No Somnium hooks found in ~/.claude/settings.json[/]")
        console.print("[yellow]Run [cyan]somnium init[/] to register them.[/]")
        return

    for event in expected:
        cmd = installed.get(event)
        if cmd:
            console.print(f"  [green]✓[/] {event:<20} [dim]→ {cmd}[/]")
        else:
            console.print(f"  [red]✗[/] {event:<20} [red]not registered[/]")


# --- MCP status ----------------------------------------------------------


def _check_mcp_server() -> dict[str, str | bool | None]:
    if shutil.which("claude") is None:
        return {"registered": False, "claude_cli": False}
    try:
        proc = subprocess.run(
            ["claude", "mcp", "get", "somnium"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"registered": False, "claude_cli": True}

    if proc.returncode != 0:
        return {"registered": False, "claude_cli": True}

    out = proc.stdout
    command = None
    connected = None
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Command:"):
            command = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("Status:"):
            connected = "Connected" in stripped
    return {"registered": True, "claude_cli": True, "command": command, "connected": connected}


def _print_mcp_status() -> None:
    console.rule("[bold]MCP server[/]")
    info = _check_mcp_server()

    if not info.get("claude_cli"):
        console.print("[yellow]Cannot check MCP status — `claude` CLI not on PATH.[/]")
        return

    if not info.get("registered"):
        console.print("[red]✗[/] somnium MCP server is [red]not registered[/]")
        console.print("[yellow]Run [cyan]somnium init[/] to register it via [cyan]claude mcp add[/].[/]")
        return

    connected = info.get("connected")
    badge = "[green]✓ Connected[/]" if connected else "[yellow]registered but not reachable[/]"
    console.print(f"  [green]✓[/] somnium {badge}")
    if info.get("command"):
        console.print(f"    [dim]command:[/] {info['command']}")
    console.print(
        "    [dim]tools exposed: memory_search, memory_write, "
        "memory_status, code_search_semantic[/]"
    )


# --- Config status -------------------------------------------------------


def _print_config_status(cfg: SomniumConfig) -> None:
    console.rule("[bold]Configuration[/]")
    api_key = cfg.embeddings.resolve_api_key()
    key_status = "[green]set[/]" if api_key else "[red]missing[/]"
    dream_status = "[green]enabled[/]" if cfg.dream.enabled else "[red]disabled[/]"
    console.print(f"  Voyage API key: {key_status}")
    console.print(
        f"    [dim]model_text=[/][cyan]{cfg.embeddings.model_text}[/]"
        f"  [dim]model_code=[/][cyan]{cfg.embeddings.model_code}[/]"
    )
    console.print(f"  Dream mode:     {dream_status}")
    console.print(f"    [dim]model=[/][cyan]{cfg.dream.model}[/]")
    if cfg.project_root:
        console.print(f"  Project root:   [cyan]{cfg.project_root}[/]")
    else:
        console.print("  Project root:   [dim](none — not in a git repo)[/]")
    console.print(f"  Global root:    [cyan]{cfg.global_root}[/]")


# --- Statusline tip ------------------------------------------------------


def _print_statusline_tip() -> None:
    settings_path = Path.home() / ".claude" / "settings.json"
    has_statusline = False
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            has_statusline = "statusLine" in data
        except Exception:  # noqa: S110
            pass
    if not has_statusline:
        console.print()
        console.print(
            "[dim]Tip: add a live status bar to Claude Code with "
            "[cyan]somnium status --install-line[/][/]"
        )


# --- Statusline install/uninstall ----------------------------------------


def _install_statusline() -> None:
    script_target = Path.home() / ".claude" / "somnium-statusline.sh"
    with resources.files("somnium.templates").joinpath("statusline.sh").open("rb") as fh:
        script_target.write_bytes(fh.read())
    script_target.chmod(0o755)
    console.print(f"[green]Wrote:[/] [cyan]{script_target}[/]")

    settings_path = Path.home() / ".claude" / "settings.json"
    settings: dict[str, object] = {}
    if settings_path.exists():
        with contextlib.suppress(json.JSONDecodeError):
            settings = json.loads(settings_path.read_text(encoding="utf-8"))

    settings["statusLine"] = {
        "type": "command",
        "command": str(script_target),
        "refreshInterval": 5,
    }
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    console.print("[green]Registered[/] status line in [cyan]~/.claude/settings.json[/]")
    console.print(
        "[dim]The status bar will appear at the bottom of Claude Code "
        "after your next message.[/]"
    )


def _uninstall_statusline() -> None:
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            if "statusLine" in settings:
                del settings["statusLine"]
                settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
                console.print("[green]Removed[/] statusLine from settings.json")
        except Exception as exc:
            console.print(f"[red]Failed to update settings.json:[/] {exc}")

    script_path = Path.home() / ".claude" / "somnium-statusline.sh"
    if script_path.exists():
        script_path.unlink()
        console.print(f"[green]Deleted:[/] {script_path}")

    console.print("[dim]Status line removed.[/]")
