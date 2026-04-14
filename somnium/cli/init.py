"""``somnium init`` command."""

from __future__ import annotations

import subprocess
import tomllib
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
# Helpers
# ------------------------------------------------------------------

_PROVIDERS = [
    questionary.Choice("Voyage AI  (remote, API key required)", value="voyage"),
    questionary.Choice("Ollama     (local, free)", value="ollama"),
]


def _ask_or_abort(question: Any) -> Any:
    """Call .ask() on a questionary question, raise typer.Abort if None."""
    result = question.ask()
    if result is None:
        raise typer.Abort
    return result


def _validate_positive_int(text: str) -> bool | str:
    """Questionary validator for positive integers."""
    try:
        val = int(text)
        if val <= 0:
            return "Must be a positive integer"
    except ValueError:
        return "Must be a positive integer"
    else:
        return True


def _template_defaults(section: str, key: str | None = None) -> Any:
    """Load a default value from the packaged templates/config.toml."""
    with resources.files("somnium.templates").joinpath("config.toml").open("rb") as fh:
        tmpl = tomllib.load(fh)
    data = tmpl.get(section, {})
    if key is not None:
        return data.get(key)
    return data


# ------------------------------------------------------------------
# Summary helpers (plain text for questionary Choice labels)
# ------------------------------------------------------------------


def _summary_embeddings(cfg: SomniumConfig) -> str:
    e = cfg.embeddings
    return f"{e.provider}, {e.model_text} / {e.model_code}"


def _summary_dream(cfg: SomniumConfig) -> str:
    d = cfg.dream
    status = "enabled" if d.enabled else "disabled"
    gate = f"gate: {d.gate.min_user_messages}+ msgs"
    if d.gate.llm_gate_enabled:
        gate += ", LLM gate on"
    return f"{status}, model={d.model}, {gate}"


def _summary_context_injection(cfg: SomniumConfig) -> str:
    c = cfg.context_injection
    status = "enabled" if c.enabled else "disabled"
    rerank = "reranker=on" if c.reranker_enabled else "reranker=off"
    return f"{status}, top_k={c.top_k}, budget={c.context_budget_tokens} tokens, {rerank}"


def _summary_code_search(cfg: SomniumConfig) -> str:
    cs = cfg.code_search
    sym = "symbolic=on" if cs.symbolic_enabled else "symbolic=off"
    sem = "semantic=on" if cs.semantic_enabled else "semantic=off"
    return f"{sym}, {sem}, chunk={cs.semantic_chunk_lines} lines"


# ------------------------------------------------------------------
# Onboarding wizard — 4-step orchestrator
# ------------------------------------------------------------------


def _run_onboarding(existing_cfg: SomniumConfig | None) -> dict[str, Any] | None:
    """Interactive onboarding. Returns a dict of config overrides to write,
    or *None* when the user chose to keep existing settings at every step."""

    console.print()
    console.print("[bold cyan]Somnium setup wizard[/]  [dim](4 steps)[/]")
    console.print("[dim]─────────────────────────────────[/]")

    overrides: dict[str, Any] = {}

    emb = _step_embeddings(existing_cfg)
    if emb is not None:
        overrides.update(emb)

    dream = _step_dream(existing_cfg)
    if dream is not None:
        overrides.update(dream)

    ctx = _step_context_injection(existing_cfg)
    if ctx is not None:
        overrides.update(ctx)

    cs = _step_code_search(existing_cfg)
    if cs is not None:
        overrides.update(cs)

    return overrides or None


# ------------------------------------------------------------------
# Step 1: Embedding provider
# ------------------------------------------------------------------


def _step_embeddings(existing_cfg: SomniumConfig | None) -> dict[str, Any] | None:
    """Step 1: Choose embedding provider and models."""
    console.print("\n[bold cyan]Step 1/4[/] [bold]Embedding provider[/]")
    console.print("[dim]Choose the vector embedding backend for memory search.[/]")

    choices: list[questionary.Choice] = []
    if existing_cfg is not None:
        choices.append(
            questionary.Choice(
                f"Keep existing settings  ({_summary_embeddings(existing_cfg)})",
                value="keep",
            )
        )
    choices.extend(_PROVIDERS)

    provider = _ask_or_abort(questionary.select(
        "Which embedding provider do you want to use?",
        choices=choices,
    ))

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
    model_text = _ask_or_abort(questionary.select(
        "Text embedding model:",
        choices=text_choices,
        default="voyage-3.5",
    ))

    model_code = _ask_or_abort(questionary.select(
        "Code embedding model:",
        choices=text_choices,
        default="voyage-code-3",
    ))

    api_key = _ask_or_abort(questionary.password(
        "Voyage API key (pa-...):",
    ))

    result: dict[str, Any] = {
        "model_text": model_text,
        "model_code": model_code,
    }
    if api_key:
        result["api_key"] = api_key

    return result


def _configure_ollama() -> dict[str, Any]:
    """Ask for Ollama-specific settings."""
    base_url = _ask_or_abort(questionary.text(
        "Ollama server URL:",
        default="http://localhost:11434",
    ))

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

    model_text = _ask_or_abort(questionary.select(
        "Text embedding model:",
        choices=model_choices,
        default=local_models[0] if local_models else "nomic-embed-text",
    ))

    model_code = _ask_or_abort(questionary.select(
        "Code embedding model (can be the same):",
        choices=model_choices,
        default=model_text,
    ))

    return {
        "model_text": model_text,
        "model_code": model_code,
        "ollama_base_url": base_url,
    }


# ------------------------------------------------------------------
# Step 2: Dream settings
# ------------------------------------------------------------------


def _step_dream(existing_cfg: SomniumConfig | None) -> dict[str, Any] | None:
    """Step 2: Dream mode settings."""
    console.print("\n[dim]─────────────────────────────────[/]")
    console.print("[bold cyan]Step 2/4[/] [bold]Dream mode[/]")
    console.print("[dim]Consolidation agent that extracts memories after each session.[/]")

    if existing_cfg is not None:
        action = _ask_or_abort(questionary.select(
            "Dream mode configuration:",
            choices=[
                questionary.Choice(
                    f"Keep existing settings  ({_summary_dream(existing_cfg)})",
                    value="keep",
                ),
                questionary.Choice("Configure dream settings", value="configure"),
            ],
        ))
        if action == "keep":
            return None

    defaults = existing_cfg.dream if existing_cfg else SomniumConfig().dream

    enabled = _ask_or_abort(questionary.confirm(
        "Enable dream mode?",
        default=defaults.enabled,
    ))

    if not enabled:
        return {"dream": {"enabled": False}}

    model = _ask_or_abort(questionary.text(
        "Dream model:",
        default=defaults.model,
    ))

    min_msgs = _ask_or_abort(questionary.text(
        "Min user messages before dream triggers:",
        default=str(defaults.gate.min_user_messages),
        validate=_validate_positive_int,
    ))

    llm_gate = _ask_or_abort(questionary.confirm(
        "Enable LLM gate? (costs ~$0.002/call, filters Q&A sessions)",
        default=defaults.gate.llm_gate_enabled,
    ))

    gate_model = defaults.gate_model
    if llm_gate:
        gate_model = _ask_or_abort(questionary.text(
            "Gate model (cheap pre-filter):",
            default=defaults.gate_model,
        ))

    # Skip patterns
    current_patterns = defaults.gate.skip_patterns
    template_patterns = _template_defaults("dream", "gate")
    if isinstance(template_patterns, dict):
        template_patterns = template_patterns.get("skip_patterns", [])
    else:
        template_patterns = []

    pattern_choices = [
        questionary.Choice("Reset to defaults", value="defaults"),
        questionary.Choice("Clear all patterns", value="clear"),
    ]
    if current_patterns:
        pattern_choices.insert(0, questionary.Choice(
            f"Keep current patterns  ({len(current_patterns)} rules)",
            value="keep",
        ))

    pattern_action = _ask_or_abort(questionary.select(
        "Skip patterns (sessions matching these bypass dreaming):",
        choices=pattern_choices,
    ))

    if pattern_action == "keep":
        skip_patterns = current_patterns
    elif pattern_action == "defaults":
        skip_patterns = template_patterns
    else:
        skip_patterns = []

    return {
        "dream": {
            "enabled": True,
            "model": model,
            "gate_model": gate_model,
            "gate": {
                "min_user_messages": int(min_msgs),
                "llm_gate_enabled": llm_gate,
                "skip_patterns": skip_patterns,
            },
        }
    }


# ------------------------------------------------------------------
# Step 3: Context injection
# ------------------------------------------------------------------


def _step_context_injection(existing_cfg: SomniumConfig | None) -> dict[str, Any] | None:
    """Step 3: Context injection settings."""
    console.print("\n[dim]─────────────────────────────────[/]")
    console.print("[bold cyan]Step 3/4[/] [bold]Context injection[/]")
    console.print("[dim]Injects relevant memories into every prompt automatically.[/]")

    if existing_cfg is not None:
        action = _ask_or_abort(questionary.select(
            "Context injection configuration:",
            choices=[
                questionary.Choice(
                    f"Keep existing settings  ({_summary_context_injection(existing_cfg)})",
                    value="keep",
                ),
                questionary.Choice("Configure context injection", value="configure"),
            ],
        ))
        if action == "keep":
            return None

    defaults = existing_cfg.context_injection if existing_cfg else SomniumConfig().context_injection

    enabled = _ask_or_abort(questionary.confirm(
        "Enable context injection?",
        default=defaults.enabled,
    ))

    if not enabled:
        return {"context_injection": {"enabled": False}}

    top_k = _ask_or_abort(questionary.text(
        "Top-K results per query:",
        default=str(defaults.top_k),
        validate=_validate_positive_int,
    ))

    budget = _ask_or_abort(questionary.text(
        "Context budget (tokens):",
        default=str(defaults.context_budget_tokens),
        validate=_validate_positive_int,
    ))

    scope_action = _ask_or_abort(questionary.select(
        "Memory scopes to search:",
        choices=[
            questionary.Choice(
                "All scopes (project, global, skills)",
                value="all",
            ),
            questionary.Choice(
                "Project + global only",
                value="project_global",
            ),
            questionary.Choice(
                "Project only",
                value="project",
            ),
        ],
    ))

    scopes_map = {
        "all": ["project", "global", "skills"],
        "project_global": ["project", "global"],
        "project": ["project"],
    }
    scopes = scopes_map[scope_action]

    reranker_enabled = _ask_or_abort(questionary.confirm(
        "Enable reranker? (Voyage only, ~$0.0001/call for better relevance)",
        default=defaults.reranker_enabled,
    ))

    reranker_model = defaults.reranker_model
    if reranker_enabled:
        reranker_model = _ask_or_abort(questionary.text(
            "Reranker model:",
            default=defaults.reranker_model,
        ))

    return {
        "context_injection": {
            "enabled": True,
            "top_k": int(top_k),
            "context_budget_tokens": int(budget),
            "scopes": scopes,
            "reranker_enabled": reranker_enabled,
            "reranker_model": reranker_model,
        }
    }


# ------------------------------------------------------------------
# Step 4: Code search
# ------------------------------------------------------------------


def _step_code_search(existing_cfg: SomniumConfig | None) -> dict[str, Any] | None:
    """Step 4: Code search settings."""
    console.print("\n[dim]─────────────────────────────────[/]")
    console.print("[bold cyan]Step 4/4[/] [bold]Code search[/]")
    console.print("[dim]Semantic and symbolic search across your codebase.[/]")

    if existing_cfg is not None:
        action = _ask_or_abort(questionary.select(
            "Code search configuration:",
            choices=[
                questionary.Choice(
                    f"Keep existing settings  ({_summary_code_search(existing_cfg)})",
                    value="keep",
                ),
                questionary.Choice("Configure code search", value="configure"),
            ],
        ))
        if action == "keep":
            return None

    defaults = existing_cfg.code_search if existing_cfg else SomniumConfig().code_search

    symbolic = _ask_or_abort(questionary.confirm(
        "Enable symbolic code search?",
        default=defaults.symbolic_enabled,
    ))

    semantic = _ask_or_abort(questionary.confirm(
        "Enable semantic code search?",
        default=defaults.semantic_enabled,
    ))

    chunk_lines = defaults.semantic_chunk_lines
    if semantic:
        chunk_lines_str = _ask_or_abort(questionary.text(
            "Chunk size (lines per chunk):",
            default=str(defaults.semantic_chunk_lines),
            validate=_validate_positive_int,
        ))
        chunk_lines = int(chunk_lines_str)

    # Ignore list
    current_ignore = defaults.ignore
    template_ignore = _template_defaults("code_search", "ignore") or []

    ignore_choices = [
        questionary.Choice(
            f"Use defaults  ({', '.join(template_ignore[:4])}{'...' if len(template_ignore) > 4 else ''})",  # noqa: PLR2004
            value="defaults",
        ),
        questionary.Choice("Clear ignore list (index everything)", value="clear"),
    ]
    if current_ignore:
        ignore_choices.insert(0, questionary.Choice(
            f"Keep current ignore list  ({len(current_ignore)} patterns)",
            value="keep",
        ))

    ignore_action = _ask_or_abort(questionary.select(
        "Ignore patterns for code indexing:",
        choices=ignore_choices,
    ))

    if ignore_action == "keep":
        ignore = current_ignore
    elif ignore_action == "defaults":
        ignore = template_ignore
    else:
        ignore = []

    return {
        "code_search": {
            "symbolic_enabled": symbolic,
            "semantic_enabled": semantic,
            "semantic_chunk_lines": chunk_lines,
            "ignore": ignore,
        }
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
    console.print()
