

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
<!-- somnium:dream:end -->
