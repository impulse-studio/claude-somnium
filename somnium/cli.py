"""Somnium CLI (typer-based)."""

from __future__ import annotations

import json
import shutil
import subprocess
from importlib import resources
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import get_config, load_config, reset_config_cache
from .hooks.install import install_hooks, uninstall_hooks
from .indexer import index_directory
from .storage.vector import VectorStore

app = typer.Typer(
    name="somnium",
    help="Second brain, RAG memory and dual code search for Claude Code.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
console = Console()


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _global_store(embedding_dim: int = 1024) -> VectorStore:
    cfg = get_config()
    return VectorStore(cfg.global_index_path, embedding_dim=embedding_dim)


def _project_store(embedding_dim: int = 1024) -> VectorStore | None:
    cfg = get_config()
    if not cfg.project_index_path:
        return None
    return VectorStore(cfg.project_index_path, embedding_dim=embedding_dim)


# ----------------------------------------------------------------------
# init
# ----------------------------------------------------------------------


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

    # --- Global -------------------------------------------------------
    global_root = cfg.global_root
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

    # --- Project ------------------------------------------------------
    if project:
        project_root = Path.cwd().resolve()
        project_dir = project_root / cfg.storage.project_marker
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

    # --- Hooks --------------------------------------------------------
    if not skip_hooks:
        console.print()
        console.print("[bold]Registering hooks in ~/.claude/settings.json[/]")
        try:
            actions = install_hooks()
            for action in actions:
                console.print(f"  {action}")
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]hook install failed:[/] {exc}")
            console.print(
                "[dim]Rerun with --skip-hooks to skip, then register "
                "them manually.[/]"
            )

    console.print()
    console.print("[bold]Next steps:[/]")
    console.print(
        f"  1. Put your Voyage key in [cyan]{global_config_path}[/] under "
        r"\[embeddings], or export [cyan]VOYAGE_API_KEY[/]."
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


# ----------------------------------------------------------------------
# index
# ----------------------------------------------------------------------


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

    if project_only and cfg.project_root is None:
        console.print(
            "[red]error:[/] no project detected (no .claude/somnium or .git found)"
        )
        raise typer.Exit(1)

    # Fail fast with a nice message if Voyage is not configured.
    if cfg.embeddings.resolve_api_key() is None:
        console.print(
            "[red]error:[/] no Voyage API key found. Set "
            "[cyan]VOYAGE_API_KEY[/] or add [cyan]api_key[/] under "
            r"\[embeddings] in your config.toml."
        )
        raise typer.Exit(1)

    if do_global:
        console.print(f"[bold]Indexing global memories[/] at [cyan]{cfg.global_memory_dir}[/]")
        with _global_store() as store:
            stats = index_directory(
                store=store,
                directory=cfg.global_memory_dir,
                kind="memory_global",
                config=cfg,
            )
            # Also index global skills if they exist as .md files
            if cfg.global_skills_dir.exists():
                skill_stats = index_directory(
                    store=store,
                    directory=cfg.global_skills_dir,
                    kind="skill_global",
                    config=cfg,
                )
                _merge_stats(stats, skill_stats)
            _print_index_stats(stats, scope="global")

    if do_project:
        assert cfg.project_memory_dir is not None
        assert cfg.project_index_path is not None
        console.print(
            f"[bold]Indexing project memories[/] at [cyan]{cfg.project_memory_dir}[/]"
        )
        with VectorStore(cfg.project_index_path) as store:
            stats = index_directory(
                store=store,
                directory=cfg.project_memory_dir,
                kind="memory_project",
                config=cfg,
            )
            # Project skills live at <repo>/.claude/skills/
            project_skills = cfg.project_root / ".claude" / "skills" if cfg.project_root else None
            if project_skills and project_skills.exists():
                skill_stats = index_directory(
                    store=store,
                    directory=project_skills,
                    kind="skill_project",
                    config=cfg,
                )
                _merge_stats(stats, skill_stats)
            _print_index_stats(stats, scope="project")

    if code:
        if cfg.project_root is None or cfg.project_code_index_path is None:
            console.print(
                "[red]error:[/] --code requires a detected project "
                "(need .git or .claude/somnium marker)"
            )
            raise typer.Exit(1)
        from .code.indexer import index_repo_code

        console.print(
            f"[bold]Indexing project code[/] at [cyan]{cfg.project_root}[/]"
        )
        with VectorStore(cfg.project_code_index_path) as store:
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


def _merge_stats(a, b) -> None:
    a.files_seen += b.files_seen
    a.files_embedded += b.files_embedded
    a.files_skipped += b.files_skipped
    a.files_deleted += b.files_deleted
    a.chunks_upserted += b.chunks_upserted


def _print_index_stats(stats, scope: str) -> None:
    console.print(
        f"  [cyan]{scope}[/]: seen [bold]{stats.files_seen}[/] files, "
        f"embedded [green]{stats.files_embedded}[/], "
        f"skipped [dim]{stats.files_skipped}[/], "
        f"deleted [red]{stats.files_deleted}[/], "
        f"chunks upserted [bold]{stats.chunks_upserted}[/]"
    )


# ----------------------------------------------------------------------
# reindex
# ----------------------------------------------------------------------


@app.command()
def reindex() -> None:
    """Alias for `index` that also walks project scope if available."""
    index()


# ----------------------------------------------------------------------
# status
# ----------------------------------------------------------------------


@app.command()
def status() -> None:
    """Show a full health snapshot: indexes, hooks, MCP server, config.

    Use this after `somnium init` to verify everything is wired up,
    and any time you want to know what's actually in your indexes
    without running a search.
    """
    reset_config_cache()
    cfg = get_config()

    _print_memory_indexes(cfg)
    _print_code_index(cfg)
    _print_hooks_status()
    _print_mcp_status()
    _print_config_status(cfg)


def _index_row(
    label: str, path: Path | None, missing_msg: str = "(not built)"
) -> tuple[str, str, str, str, str]:
    """Build one row for the indexes table."""
    if path is None:
        return (label, missing_msg, "-", "-", "-")
    if not path.exists():
        return (label, str(path), "-", "-", "-")
    with VectorStore(path) as store:
        s = store.stats()
        return (
            label,
            str(path),
            str(s["files"]),
            str(s["chunks"]),
            str(s["embedding_dim"]),
        )


def _print_memory_indexes(cfg) -> None:
    """Memories — global + project, both auto-updated by hooks."""
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


def _print_code_index(cfg) -> None:
    """Code semantic search — per-project, opt-in."""
    console.rule("[bold]Code index[/]")
    if cfg.project_code_index_path is None:
        console.print("[dim](no project detected — no code index)[/]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Index path", overflow="fold")
    table.add_column("Files", justify="right")
    table.add_column("Chunks", justify="right")

    if not cfg.project_code_index_path.exists():
        table.add_row(
            str(cfg.project_code_index_path),
            "[dim]not built[/]",
            "[dim]not built[/]",
        )
        console.print(table)
        console.print(
            "[yellow]Run [cyan]somnium index --code[/] in this repo to build it.[/]"
        )
        return

    with VectorStore(cfg.project_code_index_path) as store:
        s = store.stats()
        table.add_row(
            str(cfg.project_code_index_path),
            str(s["files"]),
            str(s["chunks"]),
        )
    console.print(table)
    console.print(
        "[dim]Updated by: PostToolUse hook (incremental, when Claude edits "
        "a source file) + manual `somnium index --code` (full rebuild).[/]"
    )


def _read_somnium_hooks_from_settings() -> dict[str, str]:
    """Return {event: command_path} for every Somnium-marked hook in
    ~/.claude/settings.json. Empty dict if the file is missing or has
    no Somnium entries."""
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
        console.print(
            "[red]No Somnium hooks found in ~/.claude/settings.json[/]"
        )
        console.print(
            "[yellow]Run [cyan]somnium init[/] to register them.[/]"
        )
        return

    for event in expected:
        cmd = installed.get(event)
        if cmd:
            console.print(f"  [green]✓[/] {event:<20} [dim]→ {cmd}[/]")
        else:
            console.print(f"  [red]✗[/] {event:<20} [red]not registered[/]")


def _check_mcp_server() -> dict[str, str | None]:
    """Query `claude mcp get somnium` and return a small status dict.

    Keys: registered (bool), command (str|None), connected (bool|None).
    Returns {"registered": False} if the claude CLI isn't on PATH.
    """
    if shutil.which("claude") is None:
        return {"registered": False, "claude_cli": False}
    try:
        proc = subprocess.run(
            ["claude", "mcp", "get", "somnium"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"registered": False, "claude_cli": True}

    if proc.returncode != 0:
        return {"registered": False, "claude_cli": True}

    out = proc.stdout
    command = None
    connected = None
    for line in out.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("Command:"):
            command = line_stripped.split(":", 1)[1].strip()
        elif line_stripped.startswith("Status:"):
            connected = "Connected" in line_stripped
    return {
        "registered": True,
        "claude_cli": True,
        "command": command,
        "connected": connected,
    }


def _print_mcp_status() -> None:
    console.rule("[bold]MCP server[/]")
    info = _check_mcp_server()

    if not info.get("claude_cli"):
        console.print(
            "[yellow]Cannot check MCP status — `claude` CLI not on PATH.[/]"
        )
        return

    if not info.get("registered"):
        console.print(
            "[red]✗[/] somnium MCP server is [red]not registered[/]"
        )
        console.print(
            "[yellow]Run [cyan]somnium init[/] to register it via "
            "[cyan]claude mcp add[/].[/]"
        )
        return

    connected = info.get("connected")
    badge = (
        "[green]✓ Connected[/]"
        if connected
        else "[yellow]registered but not reachable[/]"
    )
    console.print(f"  [green]✓[/] somnium {badge}")
    if info.get("command"):
        console.print(f"    [dim]command:[/] {info['command']}")
    console.print(
        "    [dim]tools exposed: memory_search, memory_write, "
        "memory_status, code_search_semantic[/]"
    )


def _print_config_status(cfg) -> None:
    console.rule("[bold]Configuration[/]")
    api_key = cfg.embeddings.resolve_api_key()
    key_status = "[green]set[/]" if api_key else "[red]missing[/]"
    dream_status = (
        "[green]enabled[/]" if cfg.dream.enabled else "[red]disabled[/]"
    )
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


# ----------------------------------------------------------------------
# dream (manual trigger — phase 3 implements the real thing)
# ----------------------------------------------------------------------


@app.command()
def dream(
    transcript: Path | None = typer.Option(
        None,
        "--transcript",
        "-t",
        help="Path to a transcript JSONL file. Defaults to the most recent one for the cwd.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Bypass the heuristic gate."
    ),
) -> None:
    """Manually run the dream agent on a session transcript.

    Without --transcript, picks the most recent Claude Code transcript
    associated with the current working directory.
    """
    from .dream.runner import run_dream

    reset_config_cache()
    cfg = get_config()

    if cfg.embeddings.resolve_api_key() is None:
        console.print(
            "[red]error:[/] no Voyage API key found. Dream mode needs it "
            "to reindex the memories it writes."
        )
        raise typer.Exit(1)

    if transcript is None:
        transcript = _find_latest_transcript()
        if transcript is None:
            console.print("[red]error:[/] no transcript found for this cwd")
            raise typer.Exit(1)
        console.print(f"[dim]using transcript:[/] [cyan]{transcript}[/]")

    console.print("[bold]Running dream agent[/]")
    result = run_dream(transcript_path=transcript, config=cfg, force=force)

    decision = result.gate_result.decision.value
    color = "green" if decision == "run" else "yellow"
    console.print(f"Gate: [{color}]{decision}[/] — {result.gate_result.reason}")

    if result.dream_result:
        dr = result.dream_result
        console.print(
            f"Dream agent: should_persist=[bold]{dr.should_persist}[/], "
            f"items=[bold]{len(dr.items)}[/]"
        )
        if dr.summary:
            console.print(f"Summary: [dim]{dr.summary}[/]")

    if result.write_records:
        console.print("Written:")
        for r in result.write_records:
            tag = "[green]✓[/]" if r.status in ("written", "appended") else "[yellow]~[/]"
            console.print(
                f"  {tag} [{r.category}] {r.title}"
                + (f" [dim]→ {r.path}[/]" if r.path else "")
                + (f" [red]({r.reason})[/]" if r.reason and r.status != "written" else "")
            )

    if result.error:
        console.print(f"[red]Error:[/] {result.error}")

    if result.digest_path:
        console.print(f"[dim]Digest:[/] [cyan]{result.digest_path}[/]")


def _find_latest_transcript() -> Path | None:
    """Find the most recently modified transcript JSONL for the cwd."""
    cwd = Path.cwd().resolve()
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None

    # Claude Code encodes the cwd as the project dir name: replace / with -.
    encoded = str(cwd).replace("/", "-")
    candidate_dir = projects_dir / encoded
    if not candidate_dir.exists():
        # Fall back to scanning all project dirs for the most recent file.
        candidate_dir = projects_dir

    jsonl_files = list(candidate_dir.rglob("*.jsonl"))
    if not jsonl_files:
        return None
    return max(jsonl_files, key=lambda p: p.stat().st_mtime)


# ----------------------------------------------------------------------
# search (debug/dev utility)
# ----------------------------------------------------------------------


@app.command()
def search(
    query: str = typer.Argument(..., help="Query string."),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of hits to return."),
    scope: str = typer.Option("all", "--scope", "-s", help="global|project|skills|all"),
    as_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    """Debug helper: run a memory_search from the CLI."""
    reset_config_cache()
    cfg = get_config()
    from .embeddings import get_embedder
    from .storage.scope import normalize_scopes

    embedder = get_embedder(cfg)
    query_vec = embedder.embed_query(query)
    scopes = normalize_scopes(scope)

    all_hits = []
    if cfg.global_index_path.exists():
        with _global_store() as store:
            all_hits.extend(store.search(query_vec, top_k=top_k, scopes=scopes))
    if cfg.project_index_path and cfg.project_index_path.exists():
        with VectorStore(cfg.project_index_path) as store:
            all_hits.extend(store.search(query_vec, top_k=top_k, scopes=scopes))

    all_hits.sort(key=lambda h: h.score, reverse=True)
    all_hits = all_hits[:top_k]

    if as_json:
        typer.echo(json.dumps([h.to_dict() for h in all_hits], indent=2))
        return

    if not all_hits:
        console.print("[dim]no hits[/]")
        return

    for i, hit in enumerate(all_hits, 1):
        console.print(
            f"\n[bold]{i}.[/] [green]{hit.score:.3f}[/] "
            f"[dim]{hit.scope}[/] [cyan]{hit.file_path}[/]"
        )
        preview = hit.text[:400].replace("\n", " ")
        console.print(f"   {preview}{'…' if len(hit.text) > 400 else ''}")


# ----------------------------------------------------------------------
# version
# ----------------------------------------------------------------------


@app.command()
def uninstall(
    keep_data: bool = typer.Option(
        True,
        "--keep-data/--delete-data",
        help="Keep memory files and indexes on disk (default) or delete them.",
    ),
) -> None:
    """Remove Somnium hooks from ~/.claude/settings.json and optionally
    delete the data directory."""
    console.print("[bold]Removing Somnium hooks from settings.json[/]")
    try:
        actions = uninstall_hooks()
        if actions:
            for action in actions:
                console.print(f"  {action}")
        else:
            console.print("  [dim]no Somnium hooks were registered[/]")
    except Exception as exc:  # noqa: BLE001
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


@app.command()
def version() -> None:
    """Print Somnium version."""
    console.print(f"somnium [bold]{__version__}[/]")


if __name__ == "__main__":
    app()
