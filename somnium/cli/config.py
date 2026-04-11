"""Somnium CLI: `somnium config` subcommands.

Read and write config values without editing TOML by hand.
Supports dot-notation keys like `dream.model` or `embeddings.model_code`.
"""

from __future__ import annotations

import contextlib
import tomllib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from ..config import get_config, reset_config_cache

config_app = typer.Typer(
    name="config",
    help="Read and write Somnium configuration.",
    no_args_is_help=True,
)
console = Console()


def _config_path(scope: str) -> Path:
    """Return the config file path for a scope."""
    if scope == "project":
        cfg = get_config()
        if cfg.project_root is None:
            raise typer.BadParameter("No project detected.")
        return cfg.project_root / cfg.storage.project_marker / "project.toml"
    return get_config().global_root / "config.toml"


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _is_secret_key(key: str) -> bool:
    """True if the key name looks like a secret (API key, token, etc.)."""
    lower = key.lower()
    return "api_key" in lower or "token" in lower or "secret" in lower


def _resolve_key(data: dict, key: str) -> Any:
    """Resolve a dot-notation key like 'dream.model' into the value."""
    parts = key.split(".")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _set_key(data: dict, key: str, value: str) -> dict:
    """Set a dot-notation key, creating intermediate dicts as needed.
    Tries to parse value as int/float/bool before storing as string."""
    parts = key.split(".")
    current = data
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]

    # Parse value type
    parsed: Any = value
    if value.lower() == "true":
        parsed = True
    elif value.lower() == "false":
        parsed = False
    else:
        try:
            parsed = int(value)
        except ValueError:
            with contextlib.suppress(ValueError):
                parsed = float(value)

    current[parts[-1]] = parsed
    return data


def _write_toml(path: Path, data: dict) -> None:
    """Write a dict as TOML. We do a simple manual serialization since
    tomllib is read-only and we don't want tomli-w as a dep."""
    lines: list[str] = []
    _serialize_toml(data, lines, prefix="")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _serialize_toml(data: dict, lines: list[str], prefix: str) -> None:
    """Recursively serialize a dict to TOML lines."""
    # First pass: write simple key-value pairs
    for key, value in data.items():
        if isinstance(value, dict):
            continue
        full_key = f"{prefix}{key}" if not prefix else f"{key}"
        lines.append(f"{full_key} = {_toml_value(value)}")

    # Second pass: write table sections
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        section = f"{prefix}{key}" if prefix else key
        lines.append(f"\n[{section}]")
        _serialize_toml(value, lines, prefix=f"{section}.")


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    if isinstance(v, list):
        items = ", ".join(f'"{i}"' if isinstance(i, str) else str(i) for i in v)
        return f"[{items}]"
    return f'"{v}"'


# ------------------------------------------------------------------
# get
# ------------------------------------------------------------------


@config_app.command()
def get(
    key: str = typer.Argument(help="Dot-notation key, e.g. 'dream.model' or 'embeddings.api_key_env'."),
    scope: str = typer.Option(
        "effective",
        "--scope",
        "-s",
        help="'effective' (merged), 'global', or 'project'.",
    ),
) -> None:
    """Get a config value.

    With --scope effective (default), shows the merged value that Somnium
    actually uses. With --scope global or project, shows what's written
    in that specific config file (may be empty if the key isn't overridden).
    """
    reset_config_cache()

    if scope == "effective":
        cfg = get_config()
        # Flatten the pydantic model to a dict for key resolution
        data = cfg.model_dump()
        value = _resolve_key(data, key)
    else:
        path = _config_path(scope)
        data = _load_toml(path)
        value = _resolve_key(data, key)

    if value is None:
        console.print("[dim](not set)[/]")
    elif _is_secret_key(key) and isinstance(value, str) and len(value) > 8:  # noqa: PLR2004
        console.print(f"{value[:6]}...{value[-4:]}")
    elif isinstance(value, dict):
        # Print a table for sections
        table = Table(show_header=True, header_style="bold")
        table.add_column("Key")
        table.add_column("Value")
        for k, v in value.items():
            table.add_row(f"{key}.{k}", str(v))
        console.print(table)
    else:
        console.print(str(value))


# ------------------------------------------------------------------
# set
# ------------------------------------------------------------------


@config_app.command(name="set")
def set_value(
    key: str = typer.Argument(help="Dot-notation key, e.g. 'dream.model'."),
    value: str = typer.Argument(help="Value to set. Booleans, ints, floats are auto-parsed."),
    scope: str = typer.Option(
        "global",
        "--scope",
        "-s",
        help="'global' or 'project'.",
    ),
) -> None:
    """Set a config value.

    Writes to the global config by default. Use --scope project to
    write a per-project override.

    Examples:
        somnium config set dream.model claude-haiku-4-5
        somnium config set dream.enabled false
        somnium config set context_injection.top_k 10
        somnium config set embeddings.api_key pa-... --scope global
    """
    reset_config_cache()

    if scope == "effective":
        console.print("[red]Cannot write to 'effective' — pick 'global' or 'project'.[/]")
        raise typer.Exit(1)

    path = _config_path(scope)
    data = _load_toml(path)
    _set_key(data, key, value)
    _write_toml(path, data)

    console.print(f"[green]Set[/] {key} = {value} [dim]in {path}[/]")
    reset_config_cache()


# ------------------------------------------------------------------
# list (show all effective config)
# ------------------------------------------------------------------


@config_app.command(name="list")
def list_config(
    scope: str = typer.Option(
        "effective",
        "--scope",
        "-s",
        help="'effective', 'global', or 'project'.",
    ),
) -> None:
    """List all config values."""
    reset_config_cache()

    if scope == "effective":
        cfg = get_config()
        data = cfg.model_dump()
    else:
        path = _config_path(scope)
        data = _load_toml(path)

    if not data:
        console.print(f"[dim](no config in {scope} scope)[/]")
        return

    table = Table(show_header=True, header_style="bold", title=f"Config ({scope})")
    table.add_column("Key")
    table.add_column("Value", overflow="fold")

    def _flatten(d: dict, prefix: str = "") -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for k, v in d.items():
            full = f"{prefix}{k}" if prefix else k
            if isinstance(v, dict):
                rows.extend(_flatten(v, f"{full}."))
            else:
                rows.append((full, str(v)))
        return rows

    for key, val in _flatten(data):
        # Mask API keys
        if "api_key" in key.lower() and val and len(val) > 8 and val != "None":  # noqa: PLR2004
            val = val[:6] + "..." + val[-4:]  # noqa: PLW2901
        table.add_row(key, val)

    console.print(table)


# ------------------------------------------------------------------
# path (show config file locations)
# ------------------------------------------------------------------


@config_app.command()
def path(
    scope: str = typer.Option(
        "global",
        "--scope",
        "-s",
        help="'global' or 'project'.",
    ),
) -> None:
    """Print the config file path for the given scope."""
    reset_config_cache()
    p = _config_path(scope)
    console.print(str(p))
