"""``somnium init`` command."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import typer

from ..config import load_config, reset_config_cache
from ..hooks.install import install_hooks
from . import app, console


@app.command()
def init(
    project: bool = typer.Option(
        False, "--project", help="Also create a .claude/somnium/ in the current repo."
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite existing config files."
    ),
    skip_hooks: bool = typer.Option(
        False, "--skip-hooks", help="Do not register hooks in ~/.claude/settings.json."
    ),
) -> None:
    """Create Somnium folders, copy default config, register hooks."""
    reset_config_cache()
    cfg = load_config()

    _setup_global(cfg, force=force)

    if project:
        _setup_project(cfg, force=force)

    if not skip_hooks:
        _register_hooks()

    _print_next_steps()


def _setup_global(cfg: object, *, force: bool) -> None:
    """Create global dirs and default config.toml."""
    global_root = cfg.global_root  # type: ignore[attr-defined]
    for sub in ("memory", "skills", "dream", "dream/sessions"):
        (global_root / sub).mkdir(parents=True, exist_ok=True)

    global_config_path = global_root / "config.toml"
    if not global_config_path.exists() or force:
        with resources.files("somnium.templates").joinpath("config.toml").open("rb") as fh:
            global_config_path.write_bytes(fh.read())
        console.print(f"[green]✓[/] wrote [cyan]{global_config_path}[/]")
    else:
        console.print(f"[yellow]~[/] kept existing [cyan]{global_config_path}[/]")

    console.print(f"[green]✓[/] global root ready at [cyan]{global_root}[/]")


def _setup_project(cfg: object, *, force: bool) -> None:
    """Create per-project dirs and template config."""
    project_root = Path.cwd().resolve()
    project_dir = project_root / cfg.storage.project_marker  # type: ignore[attr-defined]
    for sub in ("memory",):
        (project_dir / sub).mkdir(parents=True, exist_ok=True)

    project_config_path = project_dir / "project.toml"
    if not project_config_path.exists() or force:
        project_config_path.write_text(
            "# Somnium project overrides. Any key from the global\n"
            "# config.toml can be overridden here.\n"
            "\n"
            "# [dream]\n"
            "# enabled = true\n"
            "\n"
            "# [code_search]\n"
            "# semantic_enabled = true\n",
            encoding="utf-8",
        )
        console.print(f"[green]✓[/] wrote [cyan]{project_config_path}[/]")
    console.print(f"[green]✓[/] project dir ready at [cyan]{project_dir}[/]")


def _register_hooks() -> None:
    """Register hooks and print results."""
    console.print()
    console.print("[bold]Registering hooks in ~/.claude/settings.json[/]")
    try:
        actions = install_hooks()
        for action in actions:
            console.print(f"  {action}")
    except Exception as exc:
        console.print(f"[red]hook install failed:[/] {exc}")
        console.print(
            "[dim]Rerun with --skip-hooks to skip, then register "
            "them manually.[/]"
        )


def _print_next_steps() -> None:
    console.print()
    console.print("[bold]Next steps:[/]")
    console.print(
        "  1. Set your Voyage key: [bold]somnium config set embeddings.api_key pa-...[/]\n"
        "     or export [cyan]VOYAGE_API_KEY[/]."
    )
    console.print(
        "  2. Memory and the dream loop work automatically now — open "
        "Claude Code in any git repo and start a session. Somnium detects "
        "the project and creates its memory directory on first write."
    )
    console.print(
        "  3. [dim](Optional, per-repo)[/] For semantic code search, "
        "[cyan]cd[/] into a repo and run [bold]somnium index --code[/]. "
        "This is only needed if you want the [cyan]code_search_semantic[/] "
        "MCP tool to return hits — memory and dream mode work without it."
    )
    console.print(
        "  4. Check [bold]somnium status[/] at any time to verify every "
        "index, hook, and the MCP connection in one shot."
    )
