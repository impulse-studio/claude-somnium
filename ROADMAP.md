# Roadmap

A living list of where Somnium might go from here. Nothing in this
file is committed to a release. Ideas closer to the top are more
likely to land soon; the further down you read, the more speculative
it gets.

If you want any of this to happen, open an issue and say so — that's
the strongest signal.

## Code search

- **LSP / symbolic search via Serena.** Wrap [Serena](https://github.com/oraios/serena)
  as an MCP add-on so Claude can do `find_symbol`, `find_references`,
  and call-graph queries. This was scoped as Phase 4b and deferred for
  v0.1; semantic search alone covers most queries but symbolic is
  strictly better for "where is X defined" questions.
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

## Dream mode

- **LLM-based gate fallback.** The current gate is pure heuristic. Add
  an optional Haiku call when the heuristic is ambiguous (long session,
  no file writes — discussion or Q&A?). Bypassed by default to keep
  cost predictable.
- **Smarter dedup beyond exact title match.** Memory and skill writes
  already overwrite in place when the dream agent emits the same
  title, and the dream prompt instructs it to reuse existing titles.
  Next step: a similarity check on titles (Levenshtein or embedding
  cosine) so trivially-different phrasings (`Type hints required` vs
  `Type hints are mandatory`) collapse onto one file.
- **Cost cap per session.** Hard limit how much a single dream run can
  spend, with a config knob. Fail gracefully if the cap is hit.
- **Dream summaries.** Once a week, run a "deep dream" that condenses
  recent memories into higher-level patterns ("you've been refactoring
  the auth module a lot — here's what I've learned about it").

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

## CLI & DX

- **Memory inspection commands.**
  - `somnium memory list [--scope]` — list all memories with metadata
  - `somnium memory show <slug>` — print one memory
  - `somnium memory edit <slug>` — open in `$EDITOR` and reindex on save
  - `somnium memory rm <slug>` — delete and prune from index
- **Dream digest viewer.** `somnium dreams [--last 10]` to browse
  recent digests in the terminal with rich.
- **Cost tracking.** Log every Voyage and `claude -p` call with token
  counts and dollar amounts so users can see what Somnium is costing
  them per day / per project.
- **`somnium doctor`.** A diagnostic command that checks every piece:
  config valid? Voyage key reachable? hooks registered? MCP server
  healthy? indexes consistent? Print a clean report.
- **Setup wizard.** `somnium init --wizard` walks new users through
  picking a model, setting the API key, configuring scopes.

## Integration

- **Other MCP clients.** Somnium currently assumes Claude Code, but the
  MCP server is generic. Document and test usage with Cursor, Cline,
  and OpenWebUI.
- **VS Code extension.** A thin extension that surfaces relevant
  memories in a sidebar while you code, even when Claude isn't open.
- **Slash commands.** Wrap common dream actions as Claude Code skills:
  `/somnium-dream`, `/somnium-search`, `/somnium-memory`.

## Hosted

A managed Somnium for teams that don't want to run the infrastructure
themselves: shared memory across the team, central dashboard, billing,
SSO. Local CLI continues to work and remains the source of truth — the
hosted side is a sync target, not a replacement.

This is the most speculative item on the list. It would only happen if
there's clear demand from teams trying the open-source CLI first.

## Quality

- **Better tests for the dream pipeline.** End-to-end tests today
  require real `claude -p` and Voyage credits. Mock the sub-agent so
  the full pipeline can run in CI.
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
