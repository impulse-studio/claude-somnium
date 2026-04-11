"""``somnium uninstall`` and ``somnium install-hooks`` commands."""

from __future__ import annotations

import shutil

import typer

from ..config import get_config
from ..hooks.install import install_hooks, uninstall_hooks
from . import app, console


@app.command()
def uninstall(
    keep_data: bool = typer.Option(
        True,
        "--keep-data/--delete-data",
        help="Keep memory files and indexes on disk (default) or delete them.",
    ),
) -> None:
    """Remove Somnium hooks and optionally delete data."""
    console.print("[bold]Removing Somnium hooks from settings.json[/]")
    try:
        actions = uninstall_hooks()
        if actions:
            for action in actions:
                console.print(f"  {action}")
        else:
            console.print("  [dim]no Somnium hooks were registered[/]")
    except Exception as exc:
        console.print(f"[red]hook uninstall failed:[/] {exc}")
        raise typer.Exit(1) from exc

    if not keep_data:
        cfg = get_config()
        target = cfg.global_root
        console.print(f"[red]deleting data at[/] [cyan]{target}[/]")
        shutil.rmtree(target, ignore_errors=True)

    console.print("[green]done[/]")


@app.command(name="install-hooks")
def install_hooks_cmd() -> None:
    """Register Somnium hooks in ~/.claude/settings.json (idempotent)."""
    actions = install_hooks()
    for action in actions:
        console.print(action)
