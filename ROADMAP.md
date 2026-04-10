# Roadmap

A living list of where Somnium might go from here. Nothing in this
file is committed to a release. Ideas closer to the top are more
likely to land soon; the further down you read, the more speculative
it gets.

If you want any of this to happen, open an issue and say so — that's
the strongest signal.

## CLI & DX

- **`somnium memory edit <slug>`.** Open a memory in `$EDITOR`, then
  reindex on save. The `list`, `show`, `rm`, `move` and `merge`
  subcommands shipped in v0.0.3 — `edit` is the one remaining gap.
- **Auto-reindex after memory CLI operations.** Currently `rm`, `move`
  and `merge` print "run `somnium reindex`" — they should update the
  DuckDB index inline so the user doesn't have to.
- **Dream digest viewer.** `somnium dreams [--last 10]` to browse
  recent digests in the terminal with rich.
- **Cost tracking.** Log every Voyage and `claude -p` call with token
  counts and dollar amounts so users can see what Somnium is costing
  them per day / per project.
- **Setup wizard.** `somnium init --wizard` walks new users through
  picking a model, setting the API key, configuring scopes.
- **Legacy memory migration.** A `somnium memory migrate` one-shot
  command to rename old `2026-04-10-slug.md` files to `slug.md` and
  consolidate duplicates left over from pre-dedup versions.

## Dream mode

- **Similarity-based title dedup.** The slug-based exact match works
  well, but "Type hints required" and "Python type hint convention"
  still create two files. A Levenshtein or embedding-cosine check on
  titles before writing would collapse trivially-different phrasings
  onto one file.
- **LLM-based gate fallback.** The current gate is pure heuristic. Add
  an optional Haiku call when the heuristic is ambiguous (long session,
  no file writes — discussion or Q&A?). Bypassed by default to keep
  cost predictable.
- **Cost cap per session.** Hard limit how much a single dream run can
  spend, with a config knob. Fail gracefully if the cap is hit.
- **Dream summaries.** Once a week, run a "deep dream" that condenses
  recent memories into higher-level patterns ("you've been refactoring
  the auth module a lot — here's what I've learned about it").

## Code search

- **LSP / symbolic search via Serena.** Wrap [Serena](https://github.com/oraios/serena)
  as an MCP add-on so Claude can do `find_symbol`, `find_references`,
  and call-graph queries. Semantic search alone covers most queries but
  symbolic is strictly better for "where is X defined" questions.
- **AST-aware chunking via tree-sitter.** The current line-group
  chunker is language-agnostic but doesn't respect function or class
  boundaries. Tree-sitter would let us emit one chunk per top-level
  declaration plus one per method, which improves precision on small
  files.
- **Voyage reranker.** Add a second-stage `rerank-2-lite` pass over the
  top-K hits before returning them. Helps disambiguate noisy queries.
- **Cross-repo code search.** Today the index is per-project. A global
  index across all repos in `~/code/` (opt-in) would let Claude answer
  "where do I usually do X" across your whole codebase.

## Memory & sharing

- **Sync backends.** Today the only way to share memories with a team
  is to commit them to a git repo. Add first-class support for:
  - Git remote sync (auto-push / auto-pull a memory repo)
  - iCloud / Dropbox / Google Drive folder sync
  - A hosted backend for teams (see "Hosted" below)
- **Per-memory expiration.** Allow memories to declare a TTL or "stale
  after this date" so they don't accumulate forever.
- **Conflict resolution.** When the same memory exists at multiple
  scopes (global vs project), surface the conflict in `somnium status`
  and let the user pick which one wins.
- **Memory tags as filters.** `memory_search(query, tags=["python"])`
  to constrain searches by tag. Tags are already in frontmatter; the
  search just doesn't expose them yet.
- **Snapshot / rollback.** Periodic git-style snapshots of the memory
  directory so you can roll back if the dream agent makes a bad call.

## Embeddings

- **Multi-provider support.** Voyage is great but locks users into one
  vendor. Add provider plugins for:
  - Ollama (`nomic-embed-text` / `nomic-embed-code`) for fully local
  - OpenAI `text-embedding-3-large`
  - Cohere
  - HuggingFace transformers via local inference
- **Per-scope provider routing.** Use Voyage for code, Ollama for
  text — or any combination — via config.
- **Embedding dimension flexibility.** The DuckDB store currently locks
  the dimension at first init. Allow a `somnium reindex --new-model`
  flow that rebuilds against a different dimension safely.

## Integration

- **Slash commands for Claude Code.** Install `.md` files in
  `~/.claude/commands/somnium/` via `somnium init` so users can invoke
  `/somnium:dream`, `/somnium:search`, `/somnium:status` from inside
  Claude Code without switching to a terminal.
- **Other MCP clients.** Somnium currently assumes Claude Code, but the
  MCP server is generic. Document and test usage with Cursor, Cline,
  and OpenWebUI.
- **VS Code extension.** A thin extension that surfaces relevant
  memories in a sidebar while you code, even when Claude isn't open.

## Hosted

A managed Somnium for teams that don't want to run the infrastructure
themselves: shared memory across the team, central dashboard, billing,
SSO. Local CLI continues to work and remains the source of truth — the
hosted side is a sync target, not a replacement.

This is the most speculative item on the list. It would only happen if
there's clear demand from teams trying the open-source CLI first.

## Quality

- **Type checking with mypy.** The codebase already uses pydantic for
  config but doesn't run mypy. Add it.
- **Benchmarks.** Track indexing speed, search latency, and dream-run
  cost over time so we can spot regressions.
- **Documentation site.** Auto-deployed mkdocs from the `docs/`
  folder.

---

Want something on this list to happen sooner? Open an issue at
[impulse-studio/claude-somnium/issues](https://github.com/impulse-studio/claude-somnium/issues)
and describe the use case. The roadmap is driven by what people
actually need, not by the order things are written here.
