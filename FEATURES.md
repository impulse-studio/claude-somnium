# Features

Everything Somnium ships today.

## Memory

- **Persistent markdown memories** indexed by Voyage AI embeddings.
  Global (`~/.claude/somnium/memory/`) and per-project
  (`<repo>/.claude/somnium/memory/`) scopes, both queried together at
  search time.

- **Tag-based filtering.** Memories carry tags in YAML frontmatter.
  Filter with `memory_search(tags=["python"])` in the MCP tool or
  `somnium search --tags python,git` on the CLI. Tags are extracted
  automatically during indexing.

- **In-place updates.** Memories and skills are keyed by slug. The
  dream agent rewrites the same file rather than creating duplicates.

- **Memory management CLI.** `somnium memory list`, `show`, `rm`,
  `move`, `merge` for manual curation.

## Dream loop

- **Automatic post-session consolidation.** A detached Claude
  sub-agent reads the session transcript, classifies what's worth
  keeping, and writes memories + `CLAUDE.md` patches.

- **Heuristic gate.** Skips trivial sessions (short Q&A, single
  commits) so you don't burn tokens on nothing.

- **Per-session digest.** Every dream run produces a markdown digest
  with what was written, what was skipped, and why.

- **Dream digest viewer.** `somnium dreams [--last N]` to browse
  recent digests in the terminal. Shows what the dream agent wrote,
  when, and for which session. `--json` for machine-readable output.

- **Auto-generated project skills.** Procedural patterns get written
  as `SKILL.md` files in `<repo>/.claude/skills/`, invokable with
  `/<slug>`.

## Context injection

- **UserPromptSubmit hook.** Before Claude sees your prompt, the
  hook searches memory and attaches the most relevant chunks as
  `additionalContext`, bounded by a configurable token budget.

- **PostToolUse hook.** When Claude writes or edits a file inside a
  memory or code directory, only that file is reindexed. Hash-based
  dedup means unchanged files are a no-op.

## Code search

- **Semantic code search.** Per-project index built on demand with
  `voyage-code-3`. Exposed as the `code_search_semantic` MCP tool.

- **Incremental updates.** The PostToolUse hook keeps the code index
  current as Claude edits source files.

## Cost tracking

- **Spending visibility.** `somnium costs [--last 7d]` shows Voyage
  and `claude -p` token counts and dollar amounts.

- **Budget cap.** `dream.max_budget_usd` in config caps per-session
  dream cost.

## CLI

- **One-command setup.** `somnium init` creates folders, registers
  hooks and the MCP server.

- **One-command upgrade.** `somnium update` detects `uv` or `pipx`,
  upgrades, and re-registers hooks + MCP.

- **Health check.** `somnium status` prints indexes, hooks, MCP
  connection state, and config in one shot.

- **Slash commands.** `/somnium:dream`, `/somnium:search`,
  `/somnium:status` available inside Claude Code after init.
