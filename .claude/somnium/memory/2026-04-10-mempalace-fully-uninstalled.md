---
created_at: 2026-04-10T00:37:00.653743
category: project_memory
source: dream
tags: ["mempalace", "cleanup"]
---

# MemPalace fully uninstalled

---
name: MemPalace fully uninstalled
description: MemPalace plugin has been completely removed from this machine.
type: project
---

MemPalace v3.0.14 was fully uninstalled on 2026-04-10. Removed:
- `~/.claude/plugins/marketplaces/mempalace/`
- `~/.claude/plugins/cache/mempalace/`
- `~/.claude/plugins/data/mempalace-mempalace/`
- `~/.mempalace/` (ChromaDB + KG sqlite, was never initialized)
- Entries in `installed_plugins.json`, `known_marketplaces.json`, `settings.json`

Backup archive at `/tmp/mempalace-backup-20260409-193429.tar.gz` (7.8 KB).

**Why:** MemPalace was Somnium's predecessor on this machine. It had deep architectural gaps (no Voyage, no global/project scoping, no skill editing, Stop hook was just a counter). Replaced by Somnium.

**How to apply:** Do not reference MemPalace as an installed tool. It is gone.
