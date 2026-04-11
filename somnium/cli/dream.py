"""``somnium dream`` command."""

from __future__ import annotations

from pathlib import Path

import typer

from ..config import get_config, reset_config_cache
from . import app, console


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
            tag = "[green]✓[/]" if r.status in ("written", "appended") else "[yellow]~[/]"
            console.print(
                f"  {tag} [{r.category}] {r.title}"
                + (f" [dim]→ {r.path}[/]" if r.path else "")
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
