"""``somnium index`` and ``somnium reindex`` commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from ..config import get_config, reset_config_cache
from ..cost import set_project
from ..indexer import index_directory
from ..storage.parquet_store import ParquetStore
from . import app, console, ensure_gitattributes, global_store

if TYPE_CHECKING:
    from ..config import SomniumConfig
    from ..indexer import IndexStats


@app.command()
def index(
    project_only: bool = typer.Option(
        False, "--project", help="Only index the current project."
    ),
    global_only: bool = typer.Option(
        False, "--global", "-g", help="Only index global memories."
    ),
    code: bool = typer.Option(
        False, "--code", help="Also index the current repo's source code."
    ),
) -> None:
    """Walk memory directories, embed changed files, update the index."""
    reset_config_cache()
    cfg = get_config()

    do_global = not project_only
    do_project = not global_only and cfg.project_root is not None

    _validate_index_args(cfg, project_only=project_only)

    if do_global:
        set_project("global")
        _index_global(cfg)
    if do_project:
        set_project(cfg.project_root.name if cfg.project_root else "global")
        _index_project(cfg)
    if code:
        _index_code(cfg)


@app.command()
def reindex() -> None:
    """Alias for ``index`` that also walks project scope if available."""
    index()


# --- Helpers -------------------------------------------------------------


def _validate_index_args(cfg: SomniumConfig, *, project_only: bool) -> None:
    if project_only and cfg.project_root is None:
        console.print(
            "[red]error:[/] no project detected (no .claude/somnium or .git found)"
        )
        raise typer.Exit(1)

    if cfg.embeddings.resolve_api_key() is None:
        console.print(
            "[red]error:[/] no Voyage API key found. Set "
            "[cyan]VOYAGE_API_KEY[/] or add [cyan]api_key[/] under "
            r"\[embeddings] in your config.toml."
        )
        raise typer.Exit(1)


def _index_global(cfg: SomniumConfig) -> None:
    console.print(f"[bold]Indexing global memories[/] at [cyan]{cfg.global_memory_dir}[/]")
    with global_store() as store:
        stats = index_directory(
            store=store,
            directory=cfg.global_memory_dir,
            kind="memory_global",
            config=cfg,
        )
        if cfg.global_skills_dir.exists():
            skill_stats = index_directory(
                store=store,
                directory=cfg.global_skills_dir,
                kind="skill_global",
                config=cfg,
            )
            _merge_stats(stats, skill_stats)
        _print_index_stats(stats, scope="global")


def _index_project(cfg: SomniumConfig) -> None:
    assert cfg.project_memory_dir is not None  # noqa: S101
    assert cfg.project_index_path is not None  # noqa: S101
    assert cfg.project_root is not None  # noqa: S101
    if ensure_gitattributes(cfg.project_root):
        console.print("[green]✓[/] updated [cyan].gitattributes[/] with parquet merge driver")
    console.print(
        f"[bold]Indexing project memories[/] at [cyan]{cfg.project_memory_dir}[/]"
    )
    with ParquetStore(cfg.project_index_path) as store:
        stats = index_directory(
            store=store,
            directory=cfg.project_memory_dir,
            kind="memory_project",
            config=cfg,
        )
        project_skills = (
            cfg.project_root / ".claude" / "skills"
            if cfg.project_root
            else None
        )
        if project_skills and project_skills.exists():
            skill_stats = index_directory(
                store=store,
                directory=project_skills,
                kind="skill_project",
                config=cfg,
            )
            _merge_stats(stats, skill_stats)
        _print_index_stats(stats, scope="project")


def _index_code(cfg: SomniumConfig) -> None:
    if cfg.project_root is None or cfg.project_code_index_path is None:
        console.print(
            "[red]error:[/] --code requires a detected project "
            "(need .git or .claude/somnium marker)"
        )
        raise typer.Exit(1)

    from ..code.indexer import index_repo_code

    console.print(f"[bold]Indexing project code[/] at [cyan]{cfg.project_root}[/]")
    with ParquetStore(cfg.project_code_index_path) as store:
        code_stats = index_repo_code(
            root=cfg.project_root,
            store=store,
            config=cfg,
        )
    console.print(
        f"  [cyan]code[/]: seen [bold]{code_stats.files_seen}[/] files, "
        f"embedded [green]{code_stats.files_embedded}[/], "
        f"skipped [dim]{code_stats.files_skipped}[/], "
        f"too-large [dim]{code_stats.skipped_large}[/], "
        f"deleted [red]{code_stats.files_deleted}[/], "
        f"chunks upserted [bold]{code_stats.chunks_upserted}[/]"
    )


def _merge_stats(a: IndexStats, b: IndexStats) -> None:
    a.files_seen += b.files_seen
    a.files_embedded += b.files_embedded
    a.files_skipped += b.files_skipped
    a.files_deleted += b.files_deleted
    a.chunks_upserted += b.chunks_upserted


def _print_index_stats(stats: IndexStats, scope: str) -> None:
    console.print(
        f"  [cyan]{scope}[/]: seen [bold]{stats.files_seen}[/] files, "
        f"embedded [green]{stats.files_embedded}[/], "
        f"skipped [dim]{stats.files_skipped}[/], "
        f"deleted [red]{stats.files_deleted}[/], "
        f"chunks upserted [bold]{stats.chunks_upserted}[/]"
    )
