# Roadmap

Where Somnium is headed. Items near the top are most likely to ship
next; further down is more speculative. Open an issue if you want
something to happen sooner.

## Next up

- **Dream digest viewer.** `somnium dreams [--last 10]` to browse
  recent digests in the terminal. Shows what the dream agent wrote,
  when, and for which session.

- **Memory tags as search filters.** Tags are already in frontmatter
  but `memory_search` ignores them. Add `tags=["python"]` to the MCP
  tool and `--tags` to `somnium search`.

- **Cost tracking + cap.** Log every Voyage and `claude -p` call with
  token counts and dollar amounts. `somnium costs [--last 7d]` to see
  spending. Add `dream.max_budget_usd` config knob to cap per-session
  dream cost.

## Medium term

- **Ollama embeddings.** `nomic-embed-text` / `nomic-embed-code` for
  fully local, zero-cost embeddings. Config: `embeddings.provider = "ollama"`.

- **LLM-based gate fallback.** Optional Haiku call when the heuristic
  gate is ambiguous (long session, no file writes — discussion or Q&A?).

- **Voyage reranker.** Second-stage `rerank-2-lite` pass over top-K
  hits before injection. Improves context relevance.

- **AST-aware chunking.** Tree-sitter to split code by function/class
  boundaries instead of fixed line groups.

- **Smart forgetting.** Detect stale or contradictory memories and
  propose cleanup via `somnium memory gc`.

## Longer term

- **Cross-repo code search.** Global index across all repos in
  `~/code/` (opt-in) for "where do I usually do X" queries.

- **Per-memory expiration.** TTL or "stale after" date in frontmatter
  so memories don't accumulate forever.

- **Dream summaries.** Weekly "deep dream" that condenses recent
  memories into higher-level patterns.

- **Multi-provider embeddings.** Beyond Ollama: OpenAI
  `text-embedding-3-large`, Cohere, HuggingFace local inference.
  Per-scope provider routing.

- **Other MCP clients.** Test and document usage with Cursor, Cline,
  and OpenWebUI.

- **VS Code extension.** Sidebar showing relevant memories while you
  code, even when Claude isn't open.

## Quality

- **Benchmarks.** Track indexing speed, search latency, and dream-run
  cost over time.
- **Documentation site.** Auto-deployed mkdocs from `docs/`.

---

Want something on this list? Open an issue at
[impulse-studio/claude-somnium/issues](https://github.com/impulse-studio/claude-somnium/issues).
