"""``somnium dreams`` command."""

from __future__ import annotations

import json

import frontmatter
import typer
from rich.table import Table

from ..config import get_config
from . import app, console


def _short_cwd(cwd: str, max_len: int = 30) -> str:
    if not cwd or cwd == "-":
        return "-"
    if len(cwd) <= max_len:
        return cwd
    return "..." + cwd[-(max_len - 3) :]


@app.command()
def dreams(
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
        if category:
            cat_fmt = f"[cyan]{category}[/]"
        else:
            cat_fmt = "[dim]-[/]"

        table.add_row(ts, session, gate_fmt, cat_fmt, msgs, writes, cwd)

    console.print()
    console.print(table)
    console.print()
