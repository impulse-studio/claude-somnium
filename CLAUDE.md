

<!-- somnium:dream:start -->
<!-- somnium auto-appended 2026-04-09T23:04:15.010650 -->
## Somnium development constraints

- **Never commit API keys.** Voyage key lives in `~/.claude/somnium/config.toml` (gitignored). Pass via `VOYAGE_API_KEY=` env var in tests.
- **Hook commands must use absolute paths.** Use `_resolve_bin()` in `somnium/hooks/install.py` — never register bare command names.
- **Register MCP via `claude mcp add --scope user`**, not by editing `~/.claude/settings.json`.
- **`--bare` flag breaks OAuth** in `claude -p` calls. Use `SOMNIUM_DREAM_SUBAGENT=1` env var for recursion guard instead.
- **Structured output field**: `claude -p --json-schema ...` returns data in `envelope.structured_output`, not `envelope.result`.
- **Run tests with fake embedder**: `pytest tests/` uses synthetic vectors — no Voyage credits consumed in CI.
- **DuckDB files are derived**: deleting `*.duckdb` is safe; they rebuild from `.md` source files on next index.
<!-- somnium auto-appended 2026-04-10T14:18:25.816033 -->
- **Always commit on `dev`**, never directly on `main`. The release workflow fast-forwards `main` onto `dev`.
- **CI E2E tests use `CLAUDE_CODE_OAUTH_TOKEN`** (from `claude setup-token`), not `ANTHROPIC_API_KEY`. Run locally with `CLAUDE_CODE_OAUTH_TOKEN=... VOYAGE_API_KEY=... pytest tests/e2e/ -m live`.
- **Memory filenames are slug-only** (no date prefix): `type-hints-required.md` not `2026-04-10-type-hints-required.md`.
- **`global_skill` is not a valid dream category** — removed from schema and router. Valid categories: `project_skill`, `global_memory`, `project_memory`, `claude_md_patch`.
<!-- somnium auto-appended 2026-04-10T14:29:52.888741 -->
- **`somnium memory` subcommands** (`list`, `show`, `rm`, `merge`, `move`) live in `somnium/cli_memory.py`, registered via `app.add_typer(memory_app, name="memory")` in `cli.py`.
<!-- somnium auto-appended 2026-04-10T14:45:58.670011 -->
- **`somnium memory` subcommands** (`list`, `show`, `rm`, `merge`, `move`) live in `somnium/cli_memory.py`, registered via `app.add_typer(memory_app, name="memory")` in `cli.py`.
<!-- somnium:dream:end -->
