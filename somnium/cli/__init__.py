"""Somnium CLI (typer-based).

This package splits the CLI into one module per command group.
The ``app`` object is the top-level Typer instance registered as the
``somnium`` entry-point in pyproject.toml.
"""

from __future__ import annotations

import typer
from rich.console import Console

from ..config import get_config
from ..storage.parquet_store import ParquetStore

app = typer.Typer(
    name="somnium",
    help="Second brain, RAG memory and dual code search for Claude Code.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)
console = Console()


# --- Shared helpers used by several submodules ---------------------------


def global_store(embedding_dim: int = 1024) -> ParquetStore:
    """Open the global vector store."""
    cfg = get_config()
    return ParquetStore(cfg.global_index_path, embedding_dim=embedding_dim)


def project_store(embedding_dim: int = 1024) -> ParquetStore | None:
    """Open the project vector store (None if no project detected)."""
    cfg = get_config()
    if not cfg.project_index_path:
        return None
    return ParquetStore(cfg.project_index_path, embedding_dim=embedding_dim)


# --- Register sub-typer apps (memory, config) ----------------------------

from .config import config_app  # noqa: E402
from .memory import memory_app  # noqa: E402

app.add_typer(memory_app)
app.add_typer(config_app)

# --- Register command modules (each registers @app.command at import) ----

from . import dream as _dream  # noqa: E402
from . import index as _index  # noqa: E402
from . import init as _init  # noqa: E402
from . import search as _search  # noqa: E402
from . import status as _status  # noqa: E402
from . import uninstall as _uninstall  # noqa: E402
from . import update as _update  # noqa: E402
from . import version as _version  # noqa: E402
