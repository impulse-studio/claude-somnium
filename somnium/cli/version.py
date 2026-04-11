"""``somnium version`` command."""

from __future__ import annotations

from .. import __version__
from . import app, console


@app.command()
def version() -> None:
    """Print Somnium version."""
    console.print(f"somnium [bold]{__version__}[/]")
