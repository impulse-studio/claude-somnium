"""``somnium init`` command."""

from __future__ import annotations

import subprocess
from importlib import resources
from pathlib import Path
from typing import Any

import questionary
import typer

from ..config import SomniumConfig, load_config, reset_config_cache
from ..embeddings.base import KNOWN_MODELS, models_for_provider
from ..hooks.install import install_hooks
from . import app, console, ensure_gitattributes

# ------------------------------------------------------------------
# Onboarding wizard
# ------------------------------------------------------------------

_PROVIDERS = [
    questionary.Choice("Voyage AI  (remote, API key required)", value="voyage"),
    questionary.Choice("Ollama     (local, free)", value="ollama"),
]


def _run_onboarding(existing_cfg: SomniumConfig | None) -> dict[str, Any] | None:
    """Interactive onboarding. Returns a dict of config overrides to write,
    or *None* when the user chose to keep existing settings."""

    choices: list[questionary.Choice] = []
    if existing_cfg is not None:
        choices.append(
            questionary.Choice(
                "Use existing settings",
                value="keep",
            )
        )
    choices.extend(_PROVIDERS)

    provider = questionary.select(
        "Which embedding provider do you want to use?",
        choices=choices,
    ).ask()

    if provider is None:
        raise typer.Abort

    if provider == "keep":
        return None

    overrides: dict[str, Any] = {"embeddings": {"provider": provider}}

    if provider == "voyage":
        overrides["embeddings"].update(_configure_voyage())
    elif provider == "ollama":
        overrides["embeddings"].update(_configure_ollama())

    return overrides


def _configure_voyage() -> dict[str, Any]:
    """Ask for Voyage-specific settings."""
    voyage_models = models_for_provider("voyage")
    text_choices = [
        questionary.Choice(f"{name}  ({info.description}, {info.dim}d)", value=name)
        for name, info in voyage_models.items()
    ]
    model_text = questionary.select(
        "Text embedding model:",
        choices=text_choices,
        default="voyage-3.5",
    ).ask()
    if model_text is None:
        raise typer.Abort

    model_code = questionary.select(
        "Code embedding model:",
        choices=text_choices,
        default="voyage-code-3",
    ).ask()
    if model_code is None:
        raise typer.Abort

    api_key = questionary.password(
        "Voyage API key (pa-...):",
    ).ask()
    if api_key is None:
        raise typer.Abort

    result: dict[str, Any] = {
        "model_text": model_text,
        "model_code": model_code,
    }
    if api_key:
        result["api_key"] = api_key

    return result


def _configure_ollama() -> dict[str, Any]:
    """Ask for Ollama-specific settings."""
    base_url = questionary.text(
        "Ollama server URL:",
        default="http://localhost:11434",
    ).ask()
    if base_url is None:
        raise typer.Abort

    # Check connectivity
    from ..embeddings.ollama import check_ollama_running, list_ollama_models

    if not check_ollama_running(base_url):
        console.print(
            "[yellow]warning:[/] Ollama is not reachable at "
            f"[cyan]{base_url}[/]. Make sure Ollama is installed and running.\n"
            "  Install: [bold]curl -fsSL https://ollama.com/install.sh | sh[/]\n"
            "  Start:   [bold]ollama serve[/]"
        )

    # Build choices from local models + known catalog
    local_models = list_ollama_models(base_url)
    catalog_models = models_for_provider("ollama")

    seen: set[str] = set()
    model_choices: list[questionary.Choice] = []

    # Local models first (pulled and ready)
    for name in local_models:
        seen.add(name)
        info = KNOWN_MODELS.get(name)
        dim_str = f"{info.dim}d" if info else "?"
        desc = info.description if info else "local model"
        model_choices.append(
            questionary.Choice(f"{name}  ({desc}, {dim_str}) [pulled]", value=name)
        )

    # Catalog models not yet pulled
    for name, info in catalog_models.items():
        if name not in seen:
            model_choices.append(
                questionary.Choice(
                    f"{name}  ({info.description}, {info.dim}d) [not pulled]",
                    value=name,
                )
            )

    if not model_choices:
        model_choices.append(
            questionary.Choice("nomic-embed-text  (Nomic general-purpose, 768d)", value="nomic-embed-text")
        )

    model_text = questionary.select(
        "Text embedding model:",
        choices=model_choices,
        default=local_models[0] if local_models else "nomic-embed-text",
    ).ask()
    if model_text is None:
        raise typer.Abort

    model_code = questionary.select(
        "Code embedding model (can be the same):",
        choices=model_choices,
        default=model_text,
    ).ask()
    if model_code is None:
        raise typer.Abort

    return {
        "model_text": model_text,
        "model_code": model_code,
        "ollama_base_url": base_url,
    }


# ------------------------------------------------------------------
# Index invalidation
# ------------------------------------------------------------------


def _detect_model_change(
    old_cfg: SomniumConfig | None,
    new_overrides: dict[str, Any],
) -> bool:
    """Return True if the embedding model(s) changed."""
    if old_cfg is None:
        return False
    new_emb = new_overrides.get("embeddings", {})
    old_provider = old_cfg.embeddings.provider
    new_provider = new_emb.get("provider", old_provider)
    old_text = old_cfg.embeddings.model_text
    old_code = old_cfg.embeddings.model_code
    new_text = new_emb.get("model_text", old_text)
    new_code = new_emb.get("model_code", old_code)
    return (old_provider != new_provider) or (old_text != new_text) or (old_code != new_code)


def _invalidate_indices(cfg: SomniumConfig) -> None:
    """Delete all parquet + duckdb index files. They are derived and will
    be rebuilt on next ``somnium index``."""
    patterns = ("*.parquet", "*.duckdb", "*.duckdb.wal")
    dirs = [cfg.global_root]
    if cfg.project_dir:
        dirs.append(cfg.project_dir)
    cache_dir = Path.home() / ".claude" / "somnium" / "cache"
    if cache_dir.exists():
        dirs.append(cache_dir)

    deleted = 0
    for d in dirs:
        if not d.exists():
            continue
        for pattern in patterns:
            for f in d.rglob(pattern):
                f.unlink(missing_ok=True)
                deleted += 1
    if deleted:
        console.print(f"[yellow]~[/] deleted {deleted} index file(s) — run [bold]somnium index[/] to rebuild")


# ------------------------------------------------------------------
# Config writing
# ------------------------------------------------------------------


def _write_config(cfg: SomniumConfig, overrides: dict[str, Any]) -> None:
    """Merge overrides into the global config.toml and write it."""
    import tomllib

    global_config_path = cfg.global_root / "config.toml"

    # Read existing or load defaults
    if global_config_path.exists():
        with global_config_path.open("rb") as fh:
            existing = tomllib.load(fh)
    else:
        with resources.files("somnium.templates").joinpath("config.toml").open("rb") as fh:
            existing = tomllib.load(fh)

    # Deep merge overrides
    from ..config import _deep_merge

    merged = _deep_merge(existing, overrides)

    # Write back as TOML (manual serialization — tomllib is read-only)
    _write_toml(global_config_path, merged)
    console.print(f"[green]✓[/] wrote [cyan]{global_config_path}[/]")


def _write_toml(path: Path, data: dict[str, Any]) -> None:
    """Simple TOML writer for flat/nested dicts (no arrays of tables)."""
    lines: list[str] = []
    # Write top-level scalar keys first, then sections
    for key, value in data.items():
        if not isinstance(value, dict):
            lines.append(f"{key} = {_toml_value(value)}")

    for section, values in data.items():
        if isinstance(values, dict):
            lines.append(f"\n[{section}]")
            for k, v in values.items():
                if isinstance(v, dict):
                    lines.append(f"\n[{section}.{k}]")
                    for sk, sv in v.items():
                        lines.append(f"{sk} = {_toml_value(sv)}")
                else:
                    lines.append(f"{k} = {_toml_value(v)}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _toml_value(value: object) -> str:
    """Format a Python value as a TOML literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, list):
        inner = ", ".join(_toml_value(v) for v in value)
        return f"[{inner}]"
    return f'"{value}"'


# ------------------------------------------------------------------
# Main command
# ------------------------------------------------------------------


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
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="Keep existing settings without prompting."
    ),
) -> None:
    """Create Somnium folders, copy default config, register hooks."""
    reset_config_cache()

    # Detect whether a config already exists
    old_cfg: SomniumConfig | None = None
    try:
        candidate = load_config()
        config_path = candidate.global_root / "config.toml"
        if config_path.exists():
            old_cfg = candidate
    except Exception:  # noqa: S110
        pass

    # Onboarding
    overrides = None if non_interactive else _run_onboarding(old_cfg)

    # Setup directories first (needed for config write)
    cfg = old_cfg or load_config()
    _setup_global(cfg, force=force)

    if overrides is not None:
        # Check for model change → invalidate indices
        if _detect_model_change(old_cfg, overrides):
            console.print(
                "\n[bold yellow]Warning:[/] Embedding model changed. "
                "All existing indices are incompatible and will be deleted."
            )
            proceed = questionary.confirm("Proceed?", default=True).ask()
            if not proceed:
                raise typer.Abort
            _invalidate_indices(cfg)

        _write_config(cfg, overrides)
        reset_config_cache()
        cfg = load_config()

    if project:
        _setup_project(cfg, force=force)

    if not skip_hooks:
        _register_hooks()

    _setup_merge_driver()
    _print_next_steps(cfg)


# ------------------------------------------------------------------
# Setup helpers (unchanged logic, extracted from original)
# ------------------------------------------------------------------


def _setup_global(cfg: SomniumConfig, *, force: bool) -> None:
    """Create global dirs and default config.toml."""
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


def _setup_project(cfg: SomniumConfig, *, force: bool) -> None:
    """Create per-project dirs and template config."""
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
            "# [embeddings]\n"
            '# provider = "ollama"\n'
            '# model_text = "nomic-embed-text"\n'
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


def _register_hooks() -> None:
    """Register hooks and print results."""
    console.print()
    console.print("[bold]Registering hooks in ~/.claude/settings.json[/]")
    try:
        actions = install_hooks()
        for action in actions:
            console.print(f"  {action}")
    except Exception as exc:
        console.print(f"[red]hook install failed:[/] {exc}")
        console.print(
            "[dim]Rerun with --skip-hooks to skip, then register "
            "them manually.[/]"
        )


def _setup_merge_driver() -> None:
    """Register the Parquet cache merge driver in global git config."""
    driver_cmd = "python3 -m somnium.templates.merge_cache %O %A %B"
    try:
        subprocess.run(
            ["git", "config", "--global", "merge.somnium-cache.name",
             "Somnium embedding cache merge"],
            capture_output=True, check=False,
        )
        subprocess.run(
            ["git", "config", "--global", "merge.somnium-cache.driver", driver_cmd],
            capture_output=True, check=False,
        )
        console.print("[green]✓[/] registered merge driver [cyan]somnium-cache[/] in git config")
    except Exception as exc:
        console.print(f"[yellow]~[/] merge driver registration failed: {exc}")

    # Ensure .gitattributes exists in the current project
    cwd = Path.cwd().resolve()
    if ensure_gitattributes(cwd):
        console.print(f"[green]✓[/] ensured merge driver in [cyan]{cwd / '.gitattributes'}[/]")


def _print_next_steps(cfg: SomniumConfig) -> None:
    console.print()
    console.print("[bold]Next steps:[/]")

    step = 1
    provider = cfg.embeddings.provider
    if provider == "voyage" and not cfg.embeddings.api_key:
        console.print(
            f"  {step}. Set your Voyage key: [bold]somnium config set embeddings.api_key pa-...[/]\n"
            f"     or export [cyan]VOYAGE_API_KEY[/]."
        )
        step += 1
    elif provider == "ollama":
        model = cfg.embeddings.model_text
        console.print(
            f"  {step}. Make sure Ollama is running and pull the model:\n"
            f"     [bold]ollama pull {model}[/]"
        )
        step += 1

    console.print(
        f"  {step}. Memory and the dream loop work automatically now — open "
        "Claude Code in any git repo and start a session. Somnium detects "
        "the project and creates its memory directory on first write."
    )
    console.print(
        f"  {step + 1}. [dim](Optional, per-repo)[/] For semantic code search, "
        "[cyan]cd[/] into a repo and run [bold]somnium index --code[/]. "
        "This is only needed if you want the [cyan]code_search_semantic[/] "
        "MCP tool to return hits — memory and dream mode work without it."
    )
    console.print(
        f"  {step + 2}. Check [bold]somnium status[/] at any time to verify every "
        "index, hook, and the MCP connection in one shot."
    )
