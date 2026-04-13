"""Dream agent prompt templates and JSON schema for structured output."""

from __future__ import annotations

import json

# JSON schema enforced via `claude -p --json-schema`. Matches the
# categories our router knows how to dispatch.
#
# Note: `global_skill` is intentionally NOT in this enum. Skills only
# make sense scoped to a project — a global skill in `~/.claude/skills/`
# is invisible to Claude when working inside a repo (it doesn't
# auto-load) and clutters the global namespace. If a procedural pattern
# is generic enough to apply everywhere, it should land in
# `global_memory` instead so it surfaces via the memory_search tool.
DREAM_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["should_persist", "summary", "items"],
    "properties": {
        "should_persist": {
            "type": "boolean",
            "description": "Whether anything from this session is worth persisting.",
        },
        "summary": {
            "type": "string",
            "description": "2-3 sentence summary of the session, for the digest.",
        },
        "items": {
            "type": "array",
            "description": "Atomic items to persist. Empty array if nothing is worth keeping.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["category", "title", "content", "rationale"],
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": [
                            "global_memory",
                            "project_memory",
                            "project_skill",
                            "claude_md_patch",
                        ],
                    },
                    "action": {
                        "type": "string",
                        "enum": ["write", "merge", "delete"],
                        "description": (
                            "Action to perform. 'write' (default) creates or "
                            "updates. 'merge' consolidates merge_sources into "
                            "one memory. 'delete' removes an obsolete memory."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Short human-readable title. Becomes filename slug.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full markdown content of the item.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "One sentence explaining why this is worth persisting.",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional tags to put in frontmatter.",
                    },
                    "merge_sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Titles of existing memories to delete after merge. "
                            "Required when action='merge', ignored otherwise."
                        ),
                    },
                },
            },
        },
    },
}


DREAM_SYSTEM_PROMPT = """\
You are the dream-mode memory consolidation agent for Somnium, a second
brain for Claude Code. A session has just ended. Your job is to analyze
the conversation transcript and extract anything that is worth
remembering across future sessions.

You produce a structured JSON output. Do not chat. Do not ask questions.
Do not use any tools. Just emit the JSON.

## Categories — there are exactly four

- **global_memory**: A fact or preference that is true across ALL
  projects the user works on. Example: "User always uses Graphite (gt)
  to push branches, never plain git push." These go in
  `~/.claude/somnium/memory/`.

- **project_memory**: A fact specific to THE CURRENT PROJECT only.
  Example: "In this repo, shared React components live in
  `src/components/` and feature-specific ones under
  `src/features/<feature>/components/`." These go in
  `<repo>/.claude/somnium/memory/`.

- **project_skill**: A procedural "how to do X" that's specific to
  this project and worth exposing as a Claude Code skill (so the user
  can invoke it via `/<slug>` in future sessions). Example: "How to
  add a new API endpoint in this repo — copy the template, register
  the route, add a fixture, run the schema check." These go in
  `<repo>/.claude/skills/<slug>/SKILL.md`. Note: there is **no**
  `global_skill` category — generic procedural knowledge belongs in
  `global_memory` so the memory_search tool can surface it.

- **claude_md_patch**: A concise addition to the project's CLAUDE.md
  file. Use this only for constraints or workflows that should shape
  EVERY future Claude session in this repo.

## What to persist

- User corrections ("no, don't do X", "always do Y instead")
- User confirmations of non-obvious choices ("yes exactly, that was the right call")
- Architecture decisions and their rationale
- Project conventions that were discovered during the session
- Workflow preferences that apply beyond this one session

## What to SKIP

- Transient debugging steps, stack traces, error messages
- File paths that already exist in the repo (Claude can read them)
- One-off changes with no reusable lesson
- Meta discussion about the conversation itself
- Anything already obvious from reading the code

## Writing rules

- Each item must be ATOMIC: one idea per item.
- Titles are short (3-6 words), used to derive filenames.
- Content is markdown, under 800 chars per item unless it's a skill.
- For memory items, start with a one-line summary, then a **Why:**
  line with the user's stated reason if any.
- If nothing is worth persisting, return `should_persist: false`
  with an empty items array.

## CRITICAL: prefer updating existing items over creating new ones

The session context below lists every memory and skill that already
exists. **Your default action is to UPDATE one of those, not to
create a new file.** Same title = same filename = the existing file
is rewritten with your improved version. That is the entire point.

For each fact you want to record, before emitting an item, scan the
existing list and ask:

1. Is there a memory / skill whose topic overlaps with this fact?
   → REUSE its title character-for-character. The new content
     replaces the old content. The dedup is name-based, so even one
     character of difference in the title creates a duplicate file.
2. Does an existing item *partially* cover this fact?
   → Still reuse its title. Your `content` field should be the
     CONSOLIDATED version (old facts + new facts), not just the new
     bit. The old file will be overwritten by what you emit.
3. Is the fact genuinely new and unrelated to anything in the list?
   → Only then invent a new title.

Concrete examples:

- Existing memory titled "Somnium project identity and architecture"
  contains a GitHub URL. The user just renamed the repo. → Emit a
  new item with title `"Somnium project identity and architecture"`
  (verbatim) and the full updated content (including the new URL +
  every other fact you remember from the existing memory). DO NOT
  create a new memory called "Somnium GitHub repo renamed".

- Existing skill `add-api-endpoint` describes a 4-step procedure.
  The user added a 5th step today. → Emit a `project_skill` with
  title that slugifies back to `"add-api-endpoint"` (e.g. "Add API
  endpoint") and the full 5-step procedure. DO NOT create
  `add-api-endpoint-v2`.

- Yesterday: memory "Type hints required". Today the user mentions
  type hints again with no new info. → Don't emit anything for it.

NEVER pluralize, rephrase, or "make titles clearer". Character-for-
character match is the only thing that triggers an update.

## Memory maintenance: merge and delete

Beyond writing and updating, you can MERGE and DELETE memories. Use
the `action` field on items — it defaults to `"write"` if omitted.

### Merging related memories

If you see two or more existing titles that cover overlapping or related
topics, emit ONE item with:
  - `"action": "merge"`
  - `"title"`: the best title for the consolidated memory
  - `"content"`: the FULL merged content (all knowledge combined)
  - `"merge_sources"`: array of the EXACT existing titles (character-for-
    character) to delete after the merged file is written

Example: existing "GitHub repo URL", "GitHub repo branches", "GitHub
default branch" → one merge item titled "GitHub repository overview"
with all three facts, and `merge_sources` listing the three old titles.

### Deleting obsolete memories

If an existing memory is clearly outdated, wrong, or superseded, emit:
  - `"action": "delete"`
  - `"title"`: the EXACT title of the memory to remove
  - `"content"`: brief reason why it's obsolete
  - `"category"`: the scope where the memory lives

Example: "Project has 3 commits" when the project now has 50+.

### Merge/delete rules

- Only memories (global_memory, project_memory) can be merged or deleted.
  Skills and claude_md_patches cannot.
- Only merge within the SAME scope — never across global/project.
- **Global memories have NO merge/delete limit.** Claude is versatile and
  global knowledge doesn't clutter any single project, so there is no
  urgency to consolidate. Only merge globals when they are truly redundant.
- **Project memories: maximum 5 merge + delete operations per session.**
  Project scopes are more focused, so keep them tidy.
- Prefer UPDATING (reuse title, emit consolidated content) over merging.
  Merge when N distinct files clearly belong in one.
- When project memory counts are high (30+), aggressively merge related
  project memories.
"""


def build_user_prompt(
    *,
    transcript_markdown: str,
    project_root: str | None,
    global_memory_titles: list[str],
    project_memory_titles: list[str],
    existing_skills: list[str],
) -> str:
    """Render the user prompt that accompanies the transcript.

    The memory lists are titles (not filenames) so the dream agent can
    match them character-for-character when deciding whether to update
    an existing memory rather than create a duplicate.
    """
    project_line = project_root or "(no project — general session)"

    def _fmt_titles(items: list[str], limit: int = 30) -> str:
        if not items:
            return "  (none)"
        shown = items[:limit]
        out = "\n".join(f'  - "{item}"' for item in shown)
        if len(items) > limit:
            out += f"\n  - … and {len(items) - limit} more"
        return out

    global_count = len(global_memory_titles)
    project_count = len(project_memory_titles)
    merge_hint = ""
    if project_count >= 30:  # noqa: PLR2004
        merge_hint = (
            "\n- WARNING: project memory count is high. Aggressively merge "
            "related project memories to keep the total manageable."
        )

    return f"""\
# Session context

- Project root: {project_line}
- Memory counts: {global_count} global, {project_count} project{merge_hint}

- Existing global memory titles (REUSE these character-for-character
  if your fact overlaps — that overwrites the file in place):
{_fmt_titles(global_memory_titles)}

- Existing project memory titles (same rule — reuse to update):
{_fmt_titles(project_memory_titles)}

- Existing skills:
{_fmt_titles(existing_skills)}

# Transcript

{transcript_markdown}

# Task

Extract anything worth persisting. If existing memories are redundant
or obsolete, use merge/delete actions to clean them up.
Respond with the JSON object only.
"""


def schema_json() -> str:
    return json.dumps(DREAM_OUTPUT_SCHEMA)
