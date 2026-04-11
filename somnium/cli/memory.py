"""Somnium CLI: `somnium memory` subcommands.

Inspect, manage, merge and move memories between scopes without
leaving the terminal. Each write operation (rm, move, merge) updates
the DuckDB vector index inline so the change is immediately
searchable.
"""

from __future__ import annotations

import re
import shutil
from typing import TYPE_CHECKING

import frontmatter

if TYPE_CHECKING:
    from pathlib import Path
import typer
from rich.console import Console
from rich.table import Table

from ..config import get_config, reset_config_cache
from ..storage.parquet_store import ParquetStore

memory_app = typer.Typer(
    name="memory",
    help="List, inspect, merge and move memories.",
    no_args_is_help=True,
)
console = Console()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _gather_memories(
    scope: str | None,
) -> list[dict]:
    """Walk memory dirs and return a flat list of dicts with metadata."""
    cfg = get_config()
    entries: list[dict] = []

    dirs: list[tuple[str, Path]] = []
    if scope in (None, "global"):
        dirs.append(("global", cfg.global_memory_dir))
    if scope in (None, "project") and cfg.project_memory_dir:
        dirs.append(("project", cfg.project_memory_dir))

    for scope_label, directory in dirs:
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            if not path.is_file():
                continue
            try:
                post = frontmatter.loads(path.read_text(encoding="utf-8"))
            except Exception:
                post = frontmatter.Post("", metadata={})
            # Extract title from H1
            h1 = re.search(r"^#\s+(.+?)\s*$", post.content or "", re.MULTILINE)
            title = (
                h1.group(1).strip()
                if h1
                else post.metadata.get("title", path.stem)
            )
            tags = post.metadata.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.strip("[]").split(",")]
            entries.append(
                {
                    "scope": scope_label,
                    "slug": path.stem,
                    "title": title,
                    "tags": tags,
                    "path": path,
                    "created_at": str(post.metadata.get("created_at", "")),
                    "updated_at": str(post.metadata.get("updated_at", "")),
                    "source": str(post.metadata.get("source", "")),
                }
            )
    return entries


def _find_memory(slug: str, scope: str | None = None) -> dict | None:
    """Find a single memory by slug. Returns None if not found."""
    for m in _gather_memories(scope):
        if m["slug"] == slug:
            return m
    return None


def _store_for_scope(scope: str) -> ParquetStore | None:
    """Open the DuckDB vector store for a scope. Returns None if the
    index file doesn't exist (never built yet)."""
    cfg = get_config()
    if scope == "global":
        path = cfg.global_index_path
    elif scope == "project":
        path = cfg.project_index_path
    else:
        return None
    if path is None or not path.exists():
        return None
    return ParquetStore(path)


def _remove_from_index(file_path: Path, scope: str) -> None:
    """Remove a file from the vector index (best-effort, non-fatal)."""
    try:
        store = _store_for_scope(scope)
        if store:
            with store:
                store.delete_file(str(file_path.resolve()))
    except Exception:  # noqa: S110
        pass


def _reindex_file(file_path: Path, scope: str) -> None:
    """Reindex a single memory file (best-effort, non-fatal).

    Requires the Voyage API key to be set. If it's not, silently
    skip — the user can run `somnium reindex` later.
    """
    try:
        cfg = get_config()
        if cfg.embeddings.resolve_api_key() is None:
            return
        from ..indexer import index_single_file

        kind = "memory_global" if scope == "global" else "memory_project"
        store = _store_for_scope(scope)
        if store:
            with store:
                index_single_file(store=store, path=file_path, kind=kind, config=cfg)
    except Exception:  # noqa: S110
        pass


def _scope_dir(scope: str) -> Path:
    cfg = get_config()
    if scope == "global":
        return cfg.global_memory_dir
    if scope == "project":
        if cfg.project_memory_dir is None:
            raise typer.BadParameter("No project detected.")
        return cfg.project_memory_dir
    raise typer.BadParameter(f"Unknown scope: {scope}")


# ------------------------------------------------------------------
# list
# ------------------------------------------------------------------


@memory_app.command(name="list")
def list_memories(
    scope: str = typer.Option(
        None,
        "--scope",
        "-s",
        help="Filter by scope: global, project, or all (default).",
    ),
) -> None:
    """List all memories with their scope, title, tags and timestamps."""
    reset_config_cache()
    entries = _gather_memories(scope)

    if not entries:
        console.print("[dim]No memories found.[/]")
        raise typer.Exit(0)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Scope", width=8)
    table.add_column("Slug", overflow="fold")
    table.add_column("Title")
    table.add_column("Tags")
    table.add_column("Updated")

    for m in entries:
        tag_str = ", ".join(m["tags"]) if m["tags"] else ""
        updated = m["updated_at"][:10] if m["updated_at"] else m["created_at"][:10]
        scope_color = "cyan" if m["scope"] == "global" else "green"
        table.add_row(
            f"[{scope_color}]{m['scope']}[/]",
            m["slug"],
            m["title"],
            tag_str,
            updated,
        )

    console.print(table)
    console.print(f"\n[dim]{len(entries)} memories total[/]")


# ------------------------------------------------------------------
# show
# ------------------------------------------------------------------


@memory_app.command()
def show(
    slug: str = typer.Argument(help="The slug (filename without .md) of the memory."),
    scope: str = typer.Option(None, "--scope", "-s", help="Disambiguate if same slug in both scopes."),
) -> None:
    """Print the full content of a memory."""
    reset_config_cache()
    m = _find_memory(slug, scope)
    if not m:
        console.print(f"[red]Memory not found:[/] {slug}")
        raise typer.Exit(1)

    console.print(f"[bold]{m['title']}[/]  [dim]({m['scope']})[/]")
    console.print(f"[dim]{m['path']}[/]\n")
    console.print(m["path"].read_text(encoding="utf-8"))


# ------------------------------------------------------------------
# edit
# ------------------------------------------------------------------


@memory_app.command()
def edit(
    slug: str = typer.Argument(help="The slug of the memory to edit."),
    scope: str = typer.Option(None, "--scope", "-s"),
) -> None:
    """Open a memory in $EDITOR, then reindex it on save.

    Uses the EDITOR or VISUAL environment variable. Falls back to
    `vi` if neither is set.
    """
    import hashlib
    import os
    import subprocess

    reset_config_cache()
    m = _find_memory(slug, scope)
    if not m:
        console.print(f"[red]Memory not found:[/] {slug}")
        raise typer.Exit(1)

    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
    path = m["path"]

    # Capture hash before edit to detect changes
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    proc = subprocess.run([editor, str(path)])
    if proc.returncode != 0:
        console.print(f"[red]Editor exited with code {proc.returncode}[/]")
        raise typer.Exit(1)

    if not path.exists():
        console.print("[yellow]File was deleted by the editor.[/]")
        _remove_from_index(path, m["scope"])
        return

    after = hashlib.sha256(path.read_bytes()).hexdigest()
    if before == after:
        console.print("[dim]No changes detected.[/]")
        return

    _reindex_file(path, m["scope"])
    console.print(f"[green]Saved and reindexed:[/] {slug}")


# ------------------------------------------------------------------
# rm
# ------------------------------------------------------------------


@memory_app.command()
def rm(
    slug: str = typer.Argument(help="The slug of the memory to delete."),
    scope: str = typer.Option(None, "--scope", "-s"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Delete a memory file from disk."""
    reset_config_cache()
    m = _find_memory(slug, scope)
    if not m:
        console.print(f"[red]Memory not found:[/] {slug}")
        raise typer.Exit(1)

    if not yes:
        console.print(
            f"About to delete [bold]{m['title']}[/] "
            f"[dim]({m['scope']}, {m['path']})[/]"
        )
        confirm = typer.confirm("Continue?")
        if not confirm:
            raise typer.Abort

    _remove_from_index(m["path"], m["scope"])
    m["path"].unlink()
    console.print(f"[green]Deleted:[/] {m['slug']} ({m['scope']})")


# ------------------------------------------------------------------
# move (project ↔ global)
# ------------------------------------------------------------------


@memory_app.command()
def move(
    slug: str = typer.Argument(help="The slug of the memory to move."),
    to: str = typer.Option(
        ..., "--to", "-t", help="Target scope: 'global' or 'project'."
    ),
) -> None:
    """Move a memory between scopes (global ↔ project).

    The file is copied to the target scope and removed from the source.
    """
    reset_config_cache()
    m = _find_memory(slug)
    if not m:
        console.print(f"[red]Memory not found:[/] {slug}")
        raise typer.Exit(1)

    if m["scope"] == to:
        console.print(f"[yellow]Already in {to} scope, nothing to do.[/]")
        raise typer.Exit(0)

    target_dir = _scope_dir(to)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / m["path"].name

    if target.exists():
        console.print(
            f"[yellow]Warning:[/] {target} already exists and will be overwritten."
        )

    _remove_from_index(m["path"], m["scope"])
    shutil.move(str(m["path"]), str(target))
    _reindex_file(target, to)
    console.print(
        f"[green]Moved:[/] {slug}  "
        f"[dim]{m['scope']} → {to}[/]  "
        f"[dim]{target}[/]"
    )


# ------------------------------------------------------------------
# merge
# ------------------------------------------------------------------


@memory_app.command()
def merge(  # noqa: PLR0912, PLR0915
    slugs: list[str] = typer.Argument(help="Two or more slugs to merge."),
    title: str = typer.Option(
        None,
        "--title",
        "-t",
        help="Title for the merged memory. Defaults to the first slug's title.",
    ),
    scope: str = typer.Option(
        None,
        "--scope",
        "-s",
        help="Target scope for the merged file. Defaults to the first slug's scope. Slugs are always searched in both scopes.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Merge two or more memories into one.

    The content of all specified memories is concatenated into a single
    file, and the originals are deleted. The merged file is written to
    the same scope as the first memory (override with --scope).

    Example:
        somnium memory merge drizzle-schema drizzle-relations drizzle-queries \\
            --title "Drizzle ORM usage"
    """
    reset_config_cache()
    if len(slugs) < 2:  # noqa: PLR2004
        console.print("[red]Need at least 2 slugs to merge.[/]")
        raise typer.Exit(1)

    memories: list[dict] = []
    for slug in slugs:
        # Always search both scopes — --scope only affects output target.
        m = _find_memory(slug)
        if not m:
            console.print(f"[red]Memory not found:[/] {slug}")
            raise typer.Exit(1)
        memories.append(m)

    merged_title = title or memories[0]["title"]
    target_scope = scope or memories[0]["scope"]
    target_dir = _scope_dir(target_scope)

    # Build merged content
    sections: list[str] = []
    all_tags: set[str] = set()
    for m in memories:
        post = frontmatter.loads(m["path"].read_text(encoding="utf-8"))
        body = (post.content or "").strip()
        if body:
            sections.append(body)
        tags = post.metadata.get("tags", [])
        if isinstance(tags, list):
            all_tags.update(tags)
        elif isinstance(tags, str):
            all_tags.update(t.strip() for t in tags.strip("[]").split(","))

    merged_body = "\n\n---\n\n".join(sections)

    if not yes:
        console.print(f"[bold]Merging {len(memories)} memories into:[/]")
        console.print(f"  Title: [cyan]{merged_title}[/]")
        console.print(f"  Scope: [cyan]{target_scope}[/]")
        console.print(f"  Tags:  {', '.join(sorted(all_tags)) or '(none)'}")
        console.print("  Sources:")
        for m in memories:
            console.print(f"    - {m['slug']} ({m['scope']})")
        confirm = typer.confirm("Continue?")
        if not confirm:
            raise typer.Abort

    # Write the merged file
    import datetime as dt
    import json

    now = dt.datetime.now(tz=dt.UTC)
    slug_merged = re.sub(r"[^a-zA-Z0-9\s-]", "", merged_title).strip().lower()
    slug_merged = re.sub(r"[\s-]+", "-", slug_merged)[:60] or "merged"
    target_path = target_dir / f"{slug_merged}.md"

    fm_lines = [
        "---",
        f"created_at: {now.isoformat()}",
        f"updated_at: {now.isoformat()}",
        f"category: {'global_memory' if target_scope == 'global' else 'project_memory'}",
        "source: merge",
    ]
    if all_tags:
        fm_lines.append(f"tags: {json.dumps(sorted(all_tags))}")
    fm_lines.append("---")

    content = "\n".join(fm_lines) + f"\n\n# {merged_title}\n\n{merged_body}\n"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path.write_text(content, encoding="utf-8")

    # Delete originals (except if one has the same path as the target)
    for m in memories:
        if m["path"].resolve() != target_path.resolve() and m["path"].exists():
            _remove_from_index(m["path"], m["scope"])
            m["path"].unlink()

    _reindex_file(target_path, target_scope)

    console.print(
        f"\n[green]Merged {len(memories)} memories into:[/] "
        f"[cyan]{target_path}[/]"
    )
