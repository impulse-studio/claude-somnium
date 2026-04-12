"""``somnium search`` command."""

from __future__ import annotations

import json

import typer

from ..config import get_config, reset_config_cache
from ..storage.parquet_store import ParquetStore
from . import app, console, global_store


@app.command()
def search(
    query: str = typer.Argument(..., help="Query string."),
    top_k: int = typer.Option(5, "--top-k", "-k", help="Number of hits to return."),
    scope: str = typer.Option(
        "all",
        "--scope",
        "-s",
        help="global|project|skills|code|all",
    ),
    tags: str = typer.Option(
        "",
        "--tags",
        "-t",
        help="Comma-separated tags to filter by.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    """Search memories, skills and code from the CLI.

    Scopes:
      all      -- everything: global + project memories + skills + code
      global   -- global memories + global skills
      project  -- project memories + project skills
      skills   -- global + project skills
      code     -- per-project code index only (needs ``somnium index --code``)
    """
    reset_config_cache()
    cfg = get_config()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] or None

    include_code = scope in ("all", "code")
    include_memory = scope != "code"
    all_results: list[dict[str, object]] = []

    if include_memory:
        all_results.extend(_search_memory(cfg, query, top_k, scope, tag_list))

    if include_code:
        all_results.extend(_search_code(cfg, query, top_k, scope))

    all_results.sort(key=lambda r: r["hit"].score, reverse=True)  # type: ignore[union-attr]
    all_results = all_results[:top_k]

    if as_json:
        typer.echo(json.dumps([r["hit"].to_dict() for r in all_results], indent=2))  # type: ignore[union-attr]
        return

    if not all_results:
        console.print("[dim]no hits[/]")
        return

    _print_results(all_results)


def _search_memory(
    cfg: object, query: str, top_k: int, scope: str, tags: list[str] | None = None
) -> list[dict[str, object]]:
    from ..embeddings import get_embedder
    from ..storage.scope import normalize_scopes

    embedder = get_embedder(cfg)  # type: ignore[arg-type]
    query_vec = embedder.embed_query(query)
    scopes = normalize_scopes(scope if scope != "all" else None)
    results: list[dict[str, object]] = []

    if cfg.global_index_path.exists():  # type: ignore[attr-defined]
        with global_store() as store:
            results.extend({"type": "memory", "hit": h} for h in store.search(query_vec, top_k=top_k, scopes=scopes, tags=tags))
    if cfg.project_index_path and cfg.project_index_path.exists():  # type: ignore[attr-defined]
        with ParquetStore(cfg.project_index_path) as store:  # type: ignore[attr-defined]
            results.extend({"type": "memory", "hit": h} for h in store.search(query_vec, top_k=top_k, scopes=scopes, tags=tags))

    return results


def _search_code(
    cfg: object, query: str, top_k: int, scope: str
) -> list[dict[str, object]]:
    from ..code.semantic import search_code

    code_hits = search_code(query, top_k=top_k, config=cfg)  # type: ignore[arg-type]
    results: list[dict[str, object]] = [{"type": "code", "hit": ch} for ch in code_hits]

    if (
        not code_hits
        and scope == "code"
        and not (cfg.project_code_index_path and cfg.project_code_index_path.exists())  # type: ignore[attr-defined]
    ):
        console.print("[dim]no code hits[/] (run [cyan]somnium index --code[/] first)")

    return results


def _print_results(all_results: list[dict[str, object]]) -> None:
    for i, r in enumerate(all_results, 1):
        hit = r["hit"]
        if r["type"] == "code":
            filename = hit.file_path.split("/")[-1]  # type: ignore[union-attr]
            lang = f"[{hit.language}] " if hit.language else ""  # type: ignore[union-attr]
            lines = f":{hit.start_line}-{hit.end_line}" if hit.start_line else ""  # type: ignore[union-attr]
            console.print(
                f"\n[bold]{i}.[/] [green]{hit.score:.3f}[/] "  # type: ignore[union-attr]
                f"[dim]code[/] {lang}[cyan]{filename}{lines}[/]"
            )
            preview = hit.text.split("\n")[0][:120]  # type: ignore[union-attr]
            console.print(f"   {preview}")
        else:
            console.print(
                f"\n[bold]{i}.[/] [green]{hit.score:.3f}[/] "  # type: ignore[union-attr]
                f"[dim]{hit.scope}[/] [cyan]{hit.file_path}[/]"  # type: ignore[union-attr]
            )
            preview = hit.text[:400].replace("\n", " ")  # type: ignore[union-attr]
            console.print(f"   {preview}{'…' if len(hit.text) > 400 else ''}")  # type: ignore[union-attr]  # noqa: PLR2004
