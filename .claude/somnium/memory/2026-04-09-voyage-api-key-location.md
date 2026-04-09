---
created_at: 2026-04-09T23:04:14.610928
category: project_memory
source: dream
tags: ["credentials", "voyage", "config"]
---

# Voyage API key location

Voyage API key is stored in `~/.claude/somnium/config.toml` under `[embeddings] api_key = "pa-..."`. It is NOT committed to the repo.

For testing, it can also be passed via `VOYAGE_API_KEY=pa-...` env var.

**Why:** Key was provided during development session for live testing; kept out of git history.

**How to apply:** When running `somnium index` or any embedding-dependent operation, check config.toml exists and has the key before proceeding. Never commit it.
