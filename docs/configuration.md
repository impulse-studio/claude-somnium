# Configuration

Somnium has a single TOML config file. The default lives at
`somnium/templates/config.toml` in the repo and is copied to
`~/.claude/somnium/config.toml` on first `somnium init`.

Any key can be overridden per-project by creating
`<repo>/.claude/somnium/project.toml`. Project values win over global
values via deep merge.

You can also override the global root entirely with the `SOMNIUM_HOME`
environment variable — useful for sandboxes and tests.

## Full reference

```toml
# Embeddings provider. Only "voyage" is supported in v0.1.
[embeddings]
provider    = "voyage"
model_text  = "voyage-3.5"        # for memories
model_code  = "voyage-code-3"     # for source code
api_key_env = "VOYAGE_API_KEY"    # env var to read the key from
# api_key   = "pa-..."            # or hardcode it (not recommended)
batch_size  = 64                  # how many texts per Voyage API call

# Where global data lives. The project marker is the directory name
# Somnium looks for inside each repo to detect a project scope.
[storage]
global_root    = "~/.claude/somnium"
project_marker = ".claude/somnium"

# Dream mode (post-session memory consolidation).
[dream]
enabled    = true
model      = "claude-sonnet-4-6"   # main classification model
gate_model = "claude-haiku-4-5"    # reserved for future LLM gate

# The heuristic gate that decides whether to run the dream agent.
[dream.gate]
min_user_messages = 3
skip_patterns = [
    "^commit( this)?$",
    "^push( this)?$",
    "^run tests?$",
    "^fix the typo$",
]

# UserPromptSubmit hook — auto-injects relevant memories on every prompt.
[context_injection]
enabled               = true
top_k                 = 5
context_budget_tokens = 2000
scopes                = ["project", "global", "skills"]   # priority order

# Code search (semantic, per-project).
[code_search]
symbolic_enabled     = true     # reserved for future Serena integration
semantic_enabled     = true
semantic_chunk_lines = 40
ignore = ["node_modules", ".venv", "venv", "dist", "build", ".git"]
```

## Environment variables

| Variable | Effect |
|----------|--------|
| `VOYAGE_API_KEY` | Your Voyage AI key. Read by default if `embeddings.api_key` isn't set. |
| `SOMNIUM_HOME` | Override the global root. The default is `~/.claude/somnium`. Use this to sandbox tests. |
| `SOMNIUM_DREAM_SUBAGENT` | Set to `1` automatically by the dream runner on its sub-process; the Stop hook checks it to prevent recursion. Don't set this yourself. |

## Per-project overrides

Drop a `<repo>/.claude/somnium/project.toml` file with only the keys
you want to override. Examples:

**Disable dream mode for a sensitive client repo:**

```toml
[dream]
enabled = false
```

**Use a cheaper model for this repo:**

```toml
[dream]
model = "claude-haiku-4-5"
```

**Tighter context injection budget for a project with many small memories:**

```toml
[context_injection]
top_k                 = 3
context_budget_tokens = 800
```

**Custom code-search ignore list:**

```toml
[code_search]
ignore = ["node_modules", ".venv", "dist", "build", "vendor", "fixtures/"]
```

## Disabling individual phases

Each phase can be disabled independently without uninstalling:

| To disable… | Set… |
|-------------|------|
| Dream mode (post-session loop) | `dream.enabled = false` |
| Context injection (UserPromptSubmit) | `context_injection.enabled = false` |
| Semantic code search MCP tool | `code_search.semantic_enabled = false` |
| Hooks entirely | `somnium uninstall` (removes them from settings.json) |

The MCP server itself stays registered as long as `somnium init` has
been run, so memory tools remain available even with all hooks
disabled.

## Resetting

```bash
somnium uninstall              # remove hooks; data kept (default)
somnium uninstall --delete-data  # also wipe ~/.claude/somnium/
```

`--delete-data` is irreversible and removes all your memories,
indexes, and dream digests. Take a backup first.

## Manual edits

Both DuckDB indexes are derivable from the markdown files. Safe to do
at any time:

```bash
rm ~/.claude/somnium/index.duckdb           # nuke global index
rm <repo>/.claude/somnium/code-index.duckdb # nuke per-project code index
somnium index                               # rebuild memory
somnium index --code                        # rebuild code index
```
