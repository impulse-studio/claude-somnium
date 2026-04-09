---
created_at: 2026-04-09T23:04:14.215241
category: project_memory
source: dream
tags: ["somnium", "architecture", "project"]
---

# Somnium project identity and architecture

**Somnium** is a second-brain / RAG-memory CLI for Claude Code.

- **GitHub:** impulse-studio/claude-somnium
- **Local:** `~/Documents/temp/claude-somnium`
- **License:** Apache 2.0
- **Runtime:** Python 3.11+, pipx installable (`pipx install claude-somnium`)
- **Embeddings:** Voyage AI (`voyage-3` for text, `voyage-code-3` for code)
- **Storage:** DuckDB vector cache (derivable), Markdown as source of truth
- **Data layout:** `~/.claude/somnium/` (global) + `<repo>/.claude/somnium/` (project)
- **Five phases shipped:** memory storage, PostToolUse reindex hook, dream mode consolidation agent, semantic code search, UserPromptSubmit context injection
- **MCP tools:** `memory_search`, `memory_write`, `memory_status`, `code_search_semantic`
- **Entry points:** `somnium` CLI, `somnium-mcp`, `somnium-hook-*`, `somnium-dream-run`

**Why:** This is the canonical project being developed — all future sessions should start with this context.

**How to apply:** Orient any new session to the existing architecture before making changes.
