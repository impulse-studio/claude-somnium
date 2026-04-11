"""``somnium costs`` command."""

from __future__ import annotations

import datetime as dt

import typer
from rich.table import Table

from ..cost import read_costs, summarize_costs
from . import app, console

_PERIOD_MAP = {
    "1d": dt.timedelta(days=1),
    "7d": dt.timedelta(days=7),
    "30d": dt.timedelta(days=30),
    "90d": dt.timedelta(days=90),
}


_1K = 1_000
_1M = 1_000_000


def _fmt_tokens(n: int) -> str:
    if n >= _1M:
        return f"{n / _1M:.1f}M"
    if n >= _1K:
        return f"{n / _1K:.1f}k"
    return str(n)


@app.command()
def costs(
    last: str = typer.Option(
        "30d",
        "--last",
        "-l",
        help="Time period: 1d, 7d, 30d, 90d, or all.",
    ),
    source: str = typer.Option(
        None,
        "--source",
        "-s",
        help="Filter by source: voyage, dream, or all (default).",
    ),
) -> None:
    """Show Somnium cost breakdown (Voyage API + dream agent)."""
    since = None
    if last != "all":
        delta = _PERIOD_MAP.get(last)
        if delta is None:
            console.print(f"[red]Unknown period:[/] {last}. Use 1d, 7d, 30d, 90d, or all.")
            raise typer.Exit(1)
        since = dt.datetime.now(tz=dt.UTC) - delta

    entries = read_costs(since=since, source=source)

    if not entries:
        period = f"last {last}" if last != "all" else "all time"
        console.print(f"[dim]No cost data for {period}.[/]")
        return

    summary = summarize_costs(entries)
    period_label = f"last {last}" if last != "all" else "all time"

    table = Table(
        show_header=True,
        header_style="bold",
        title=f"Somnium costs ({period_label})",
        show_footer=True,
        footer_style="bold",
    )
    table.add_column("Time", width=11, footer="Total")
    table.add_column("Src", width=5)
    table.add_column("Project", max_width=14, overflow="ellipsis")
    table.add_column("Model", max_width=16, overflow="ellipsis")
    table.add_column("Tokens", justify="right", footer=_fmt_tokens(summary["total_tokens"]))
    table.add_column("Cost", justify="right", footer=f"${summary['total_usd']:.4f}")

    today = dt.datetime.now(tz=dt.UTC).date().isoformat()
    for e in entries:
        raw_ts = e.get("ts", "")[:16].replace("T", " ")
        ts = raw_ts[11:] if raw_ts[:10] == today else raw_ts[:10]
        source_label = e.get("source", "")
        project = e.get("project", "global")
        model = e.get("model", "")
        tokens = e.get("tokens", 0)
        cost = e.get("cost_usd", 0)

        # Color source
        if source_label == "dream":
            source_fmt = f"[magenta]{source_label}[/]"
        elif source_label == "index":
            source_fmt = f"[cyan]{source_label}[/]"
        else:
            source_fmt = source_label

        # Color project
        project_fmt = f"[dim]{project}[/]" if project == "global" else f"[green]{project}[/]"

        table.add_row(
            ts,
            source_fmt,
            project_fmt,
            model,
            _fmt_tokens(tokens),
            f"${cost:.4f}",
        )

    console.print()
    console.print(table)
    console.print()
