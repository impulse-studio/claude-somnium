"""``somnium dream`` subcommands: run the dream agent and browse digests."""

from __future__ import annotations

import json
from pathlib import Path

import frontmatter
import typer
from rich.console import Console
from rich.table import Table

from ..config import get_config, reset_config_cache

dream_app = typer.Typer(
    name="dream",
    help="Run the dream agent or browse session digests.",
    no_args_is_help=True,
)
console = Console()


# ---------------------------------------------------------------------------
# somnium dream run
# ---------------------------------------------------------------------------


@dream_app.command()
def run(
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
    """Manually run the dream agent on a session transcript."""
    from ..dream.runner import run_dream

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

    _print_dream_result(result)


def _print_dream_result(result: object) -> None:
    """Print dream result summary."""
    decision = result.gate_result.decision.value  # type: ignore[attr-defined]
    color = "green" if decision == "run" else "yellow"
    console.print(f"Gate: [{color}]{decision}[/] — {result.gate_result.reason}")  # type: ignore[attr-defined]

    if result.dream_result:  # type: ignore[attr-defined]
        dr = result.dream_result  # type: ignore[attr-defined]
        console.print(
            f"Dream agent: should_persist=[bold]{dr.should_persist}[/], "
            f"items=[bold]{len(dr.items)}[/]"
        )
        if dr.summary:
            console.print(f"Summary: [dim]{dr.summary}[/]")

    if result.write_records:  # type: ignore[attr-defined]
        console.print("Written:")
        for r in result.write_records:  # type: ignore[attr-defined]
            tag = "[green]\u2713[/]" if r.status in ("written", "appended") else "[yellow]~[/]"
            console.print(
                f"  {tag} [{r.category}] {r.title}"
                + (f" [dim]\u2192 {r.path}[/]" if r.path else "")
                + (f" [red]({r.reason})[/]" if r.reason and r.status != "written" else "")
            )

    if result.error:  # type: ignore[attr-defined]
        console.print(f"[red]Error:[/] {result.error}")  # type: ignore[attr-defined]

    if result.digest_path:  # type: ignore[attr-defined]
        console.print(f"[dim]Digest:[/] [cyan]{result.digest_path}[/]")  # type: ignore[attr-defined]


def _find_latest_transcript() -> Path | None:
    """Find the most recently modified transcript JSONL for the cwd."""
    cwd = Path.cwd().resolve()
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.exists():
        return None

    encoded = str(cwd).replace("/", "-")
    candidate_dir = projects_dir / encoded
    if not candidate_dir.exists():
        candidate_dir = projects_dir

    jsonl_files = list(candidate_dir.rglob("*.jsonl"))
    if not jsonl_files:
        return None
    return max(jsonl_files, key=lambda p: p.stat().st_mtime)


# ---------------------------------------------------------------------------
# somnium dream list
# ---------------------------------------------------------------------------


def _short_cwd(cwd: str, max_len: int = 30) -> str:
    if not cwd or cwd == "-":
        return "-"
    if len(cwd) <= max_len:
        return cwd
    return "..." + cwd[-(max_len - 3) :]


@dream_app.command(name="list")
def list_digests(  # noqa: PLR0912
    last: int = typer.Option(
        10,
        "--last",
        "-n",
        help="Number of recent digests to show.",
    ),
    all_: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="Show all digests (overrides --last).",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON array.",
    ),
) -> None:
    """Browse recent dream session digests."""
    cfg = get_config()
    sessions_dir = cfg.dream_dir / "sessions"

    if not sessions_dir.exists():
        if json_output:
            console.print("[]")
        else:
            console.print("[dim]No dream sessions found.[/]")
        return

    files = sorted(sessions_dir.glob("*.md"), reverse=True)

    if not files:
        if json_output:
            console.print("[]")
        else:
            console.print("[dim]No dream sessions found.[/]")
        return

    if not all_:
        files = files[:last]

    digests: list[dict] = []
    for f in files:
        post = frontmatter.load(str(f))
        meta = dict(post.metadata)
        meta["file"] = str(f)
        digests.append(meta)

    if json_output:
        print(json.dumps(digests, default=str))
        return

    table = Table(
        show_header=True,
        header_style="bold",
        title=f"Dream digests ({len(digests)} shown)",
    )
    table.add_column("Date", width=16)
    table.add_column("Session", width=10)
    table.add_column("Gate", width=6)
    table.add_column("Category", width=16)
    table.add_column("Msgs", justify="right", width=5)
    table.add_column("Writes", justify="right", width=6)
    table.add_column("CWD", max_width=30, overflow="ellipsis")

    for d in digests:
        ts = str(d.get("timestamp", ""))[:16].replace("T", " ")
        session = str(d.get("session_id", ""))[:8]
        gate = str(d.get("gate_decision", ""))
        category = str(d.get("category", ""))
        msgs = str(d.get("user_messages", ""))
        writes = str(d.get("file_writes", ""))
        cwd = _short_cwd(str(d.get("cwd", "-")))

        # Color gate
        if gate == "run":
            gate_fmt = f"[green]{gate}[/]"
        elif gate == "skip":
            gate_fmt = f"[dim]{gate}[/]"
        else:
            gate_fmt = gate

        # Color category
        cat_fmt = f"[cyan]{category}[/]" if category else "[dim]-[/]"

        table.add_row(ts, session, gate_fmt, cat_fmt, msgs, writes, cwd)

    console.print()
    console.print(table)
    console.print()
