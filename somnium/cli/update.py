"""``somnium update`` command."""

from __future__ import annotations

import shutil
import subprocess

import questionary
import typer

from ..config import load_config, reset_config_cache
from ..hooks.install import install_hooks
from . import app, console


@app.command()
def update(
    skip_init: bool = typer.Option(
        False, "--skip-init", help="Only upgrade the package, don't re-register hooks."
    ),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Skip reconfiguration prompt after upgrade."
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
        _reinit(interactive=not non_interactive)


def _reinit(*, interactive: bool = False) -> None:
    """Re-setup directories and hooks after upgrade."""
    console.print()
    console.print("[bold]Re-initializing[/]")
    reset_config_cache()
    cfg = load_config()

    # Ensure global directories exist (new versions may add new subdirs)
    from .init import _setup_global

    _setup_global(cfg, force=False)

    # Optionally offer reconfiguration
    if interactive:
        reconfigure = questionary.confirm(
            "Would you like to reconfigure settings?",
            default=False,
        ).ask()
        if reconfigure:
            from .init import (
                _detect_model_change,
                _invalidate_indices,
                _run_onboarding,
                _write_config,
            )

            overrides = _run_onboarding(cfg)
            if overrides is not None:
                if _detect_model_change(cfg, overrides):
                    console.print(
                        "\n[bold yellow]Warning:[/] Embedding model changed. "
                        "All existing indices will be deleted."
                    )
                    proceed = questionary.confirm("Proceed?", default=True).ask()
                    if not proceed:
                        raise typer.Abort
                    _invalidate_indices(cfg)

                _write_config(cfg, overrides)
                reset_config_cache()

    # Re-register hooks with new binary paths
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
