"""``somnium update`` command."""

from __future__ import annotations

import shutil
import subprocess

import typer

from ..config import reset_config_cache
from ..hooks.install import install_hooks
from . import app, console


@app.command()
def update(
    skip_init: bool = typer.Option(
        False, "--skip-init", help="Only upgrade the package, don't re-register hooks."
    ),
) -> None:
    """Upgrade Somnium to the latest version and re-register hooks."""
    pkg = "claude-somnium"
    manager = _detect_installer(pkg)

    if manager is None:
        console.print(
            "[red]error:[/] could not detect how Somnium was installed "
            "(neither `uv tool` nor `pipx` found it). "
            "Upgrade manually and rerun [cyan]somnium init[/]."
        )
        raise typer.Exit(1)

    cmd = ["uv", "tool", "upgrade", pkg] if manager == "uv" else ["pipx", "upgrade", pkg]

    console.print(f"[bold]Upgrading via {manager}[/]: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        console.print(f"[red]upgrade failed:[/]\n{result.stderr.strip()}")
        raise typer.Exit(1)

    console.print(result.stdout.strip())
    console.print("[green]✓[/] upgrade complete")

    if not skip_init:
        _reregister_hooks()


def _reregister_hooks() -> None:
    console.print()
    console.print("[bold]Re-registering hooks + MCP server[/]")
    reset_config_cache()
    try:
        actions = install_hooks()
        for action in actions:
            console.print(f"  {action}")
    except Exception as exc:
        console.print(f"[red]hook install failed:[/] {exc}")
    console.print("[green]✓[/] init complete")


def _detect_installer(pkg: str) -> str | None:
    """Return 'uv' or 'pipx' depending on which tool manages the package."""
    if shutil.which("uv"):
        try:
            proc = subprocess.run(
                ["uv", "tool", "list"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if proc.returncode == 0 and pkg in proc.stdout:
                return "uv"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    if shutil.which("pipx"):
        try:
            proc = subprocess.run(
                ["pipx", "list", "--short"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if proc.returncode == 0 and pkg in proc.stdout:
                return "pipx"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return None
