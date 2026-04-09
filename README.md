# Somnium

**Second brain, RAG memory and semantic code search for Claude Code.**

Somnium is a CLI + MCP server + hook bundle that gives Claude Code a
persistent memory layer backed by vector search, and a "dream mode" that
analyzes your sessions after they end and auto-updates your memories,
skills and `CLAUDE.md` files.

> Status: alpha. Phases 1–5 working end-to-end. Serena-based symbolic
> code search (phase 4b) is deferred.

## Features

| Phase | Feature | Status |
|---|---|---|
| 1 | Markdown-first memory store + Voyage AI embeddings + DuckDB vector index | ✅ |
| 1 | MCP server: `memory_search`, `memory_write`, `memory_status` | ✅ |
| 2 | `PostToolUse` hook: incremental reindex when Claude writes/edits a file | ✅ |
| 2 | Hook installer: `somnium init` wires settings.json idempotently | ✅ |
| 3 | Dream mode: `Stop` hook → gate → `claude -p` sub-agent → router → files | ✅ |
| 3 | Auto-routing: global memory / project memory / skills / `CLAUDE.md` patches | ✅ |
| 3 | Per-session digest markdown | ✅ |
| 4 | Semantic code search via `voyage-code-3` + MCP `code_search_semantic` | ✅ |
| 4b | Symbolic code search via Serena/LSP | deferred |
| 5 | `UserPromptSubmit` hook: light context injection with token budget | ✅ |

- **Markdown-first memory.** Memories live as `.md` files you can read,
  edit and version. A local DuckDB vector index is a derivable cache.
- **Voyage AI embeddings.** Uses `voyage-3.5` for text and `voyage-code-3`
  for code, configurable via `~/.claude/somnium/config.toml`.
- **Dream mode.** A Stop-hook spawns a detached Claude sub-agent that
  reviews your session, classifies what is worth persisting (global
  memory, project memory, skill, `CLAUDE.md` patch, or trash) and
  auto-edits the right files. Review via git for anything in a repo.
- **Semantic code search.** Per-project on-demand index via
  `somnium index --code`, queryable through the MCP server.
- **Light context injection.** A `UserPromptSubmit` hook runs a
  top-K memory search for every new prompt and injects the hits into
  the turn's context, bounded by a token budget.
- **Recursion-safe.** The dream sub-agent carries an env var marker so
  our own Stop hook detects it and no-ops, preventing infinite loops.

## Scoping

- **Global memory** lives in `~/.claude/somnium/memory/` and applies
  across every repo.
- **Project memory** lives in `<repo>/.claude/somnium/memory/` and is
  scoped to that repo. It merges with global at query time.

## Install

```bash
pipx install claude-somnium
somnium init                    # creates ~/.claude/somnium/ and wires hooks
somnium init --project          # optional, adds .claude/somnium/ to current repo
```

Then set your Voyage API key in `~/.claude/somnium/config.toml` or via
the `VOYAGE_API_KEY` environment variable. Get one at
[voyageai.com](https://www.voyageai.com/).

## Usage

```bash
somnium init                    # create ~/.claude/somnium and install hooks
somnium init --project          # also add .claude/somnium to the current repo
somnium index                   # build/refresh the global memory index
somnium index --code            # index the current project's source code
somnium reindex                 # re-check all files and upsert changes
somnium search "query"          # debug CLI search
somnium status                  # show counts and health
somnium dream [-t path] [--force]  # manually run the dream agent
somnium uninstall               # remove hooks from settings.json
```

Once installed, Claude Code will auto-load the Somnium MCP server and
its hooks. Claude can call:

- `memory_search(query, scope, top_k)` — semantic search on your memories
- `memory_write(content, scope, title, tags)` — append a memory
- `memory_status()` — health snapshot
- `code_search_semantic(query, top_k)` — embedding-based code search

## How the dream mode works

1. You finish a Claude Code session (hit the Stop).
2. The `Stop` hook fires `somnium-hook-stop` which runs the **gate**:
   - < N user messages → skip.
   - every user message matches a trivial pattern → skip.
   - no file writes and short Q&A → skip.
   - otherwise → dispatch a **detached** `somnium-dream-run` subprocess.
3. `somnium-dream-run` spawns `claude -p` with:
   - the dream system prompt + your session transcript,
   - `--output-format json` and `--json-schema`,
   - `SOMNIUM_DREAM_SUBAGENT=1` env var (prevents hook recursion).
4. The sub-agent returns a classified JSON payload.
5. The **router** writes each item to the right location and triggers
   incremental reindex.
6. A per-session digest markdown is written to
   `~/.claude/somnium/dream/sessions/`.

Typical dream run cost with Sonnet 4.6: ~$0.08–0.12 per session.
Configure `dream.model` to `claude-haiku-4-5` in your config to
bring that down.

## Configuration

See `somnium/templates/config.toml` for the full default config. Any
key can be overridden per-project in `<repo>/.claude/somnium/project.toml`.

Key knobs:

```toml
[embeddings]
api_key_env = "VOYAGE_API_KEY"   # or put `api_key = "..."` directly
model_text = "voyage-3.5"
model_code = "voyage-code-3"

[dream]
enabled = true
model = "claude-sonnet-4-6"
gate_model = "claude-haiku-4-5"

[dream.gate]
min_user_messages = 3
skip_patterns = ["^commit( this)?$", "^push( this)?$", "^run tests?$"]

[context_injection]
enabled = true
top_k = 5
context_budget_tokens = 2000
scopes = ["project", "global", "skills"]

[code_search]
semantic_chunk_lines = 40
ignore = ["node_modules", ".venv", "dist", "build"]
```

## Configuration

See `somnium/templates/config.toml` for the full default config. You
can override any key per-project in `<repo>/.claude/somnium/project.toml`.

## Architecture

```
somnium/
├── cli.py               # typer app — user-facing CLI
├── config.py            # pydantic config, global/project merge, SOMNIUM_HOME
├── indexer.py           # markdown indexer orchestrator
├── mcp_server.py        # FastMCP server — tools for Claude Code
├── storage/
│   ├── markdown.py      # frontmatter parsing, H1/H2/H3 chunking
│   ├── vector.py        # DuckDB vector store (cosine similarity)
│   └── scope.py         # Scope enum + normalization
├── embeddings/
│   └── voyage.py        # Voyage AI wrapper with batching + retry
├── code/
│   ├── chunker.py       # Source code line-group chunking
│   ├── walker.py        # Repo walker with ignore rules
│   ├── indexer.py       # Code index builder
│   └── semantic.py      # Query interface
├── hooks/
│   ├── install.py       # Idempotent settings.json editor
│   ├── _common.py       # stdin reader, logging, path routing
│   ├── post_tool_use.py # Memory + code incremental reindex
│   ├── stop.py          # Dream gate dispatcher
│   └── user_prompt_submit.py  # Context injection
└── dream/
    ├── transcript.py    # Parse Claude Code JSONL transcripts
    ├── gate.py          # Heuristic decisioning
    ├── prompts.py       # System prompt + JSON schema
    ├── agent.py         # Spawn `claude -p` sub-agent
    ├── router.py        # Dispatch items to files
    ├── digest.py        # Per-session markdown digest
    ├── runner.py        # Pipeline orchestration
    └── cli_runner.py    # Detached runner entry point
```

## Testing

```bash
pip install -e '.[dev]'
pytest
```

The test suite uses a fake embedder so it runs in ~1s without touching
the Voyage API.

## License

Apache-2.0.
