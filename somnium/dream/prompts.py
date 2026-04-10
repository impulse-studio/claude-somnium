"""Dream agent prompt templates and JSON schema for structured output."""

from __future__ import annotations

import json

# JSON schema enforced via `claude -p --json-schema`. Matches the
# categories our router knows how to dispatch.
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
                            "global_skill",
                            "project_skill",
                            "claude_md_patch",
                        ],
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

## Categories

- **global_memory**: A fact or preference that is true across ALL
  projects the user works on. Example: "User always uses Graphite (gt)
  to push branches, never plain git push." These go in
  `~/.claude/somnium/memory/`.

- **project_memory**: A fact specific to THE CURRENT PROJECT only.
  Example: "In this repo, shared React components live in
  `src/components/` and feature-specific ones under
  `src/features/<feature>/components/`." These go in
  `<repo>/.claude/somnium/memory/`.

- **global_skill**: A procedural "how to do X" that is reusable across
  projects and worth exposing as a Claude Code skill. Example: "How to
  review a PR with emphasis on security." Format as a skill with
  triggers and steps.

- **project_skill**: Same as global_skill but specific to the current
  project.

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

## CRITICAL: Reuse titles to update existing memories

The session context below lists memories that already exist. **If the
fact you want to record overlaps with one of them, REUSE THE EXACT
TITLE of the existing memory.** Same title = same filename = the
existing file gets overwritten with your improved version.

Do NOT invent a new title for a concept that's already covered.
Do NOT pluralize, rephrase, or "make it clearer" — match the title
character-for-character. The whole point is to update in place rather
than accumulate near-duplicates.

Only invent a new title when the fact is genuinely new and unrelated
to anything in the existing list.
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

    def _fmt_titles(items: list[str], max: int = 30) -> str:
        if not items:
            return "  (none)"
        shown = items[:max]
        out = "\n".join(f'  - "{item}"' for item in shown)
        if len(items) > max:
            out += f"\n  - … and {len(items) - max} more"
        return out

    return f"""\
# Session context

- Project root: {project_line}

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

Extract anything worth persisting. Respond with the JSON object only.
"""


def schema_json() -> str:
    return json.dumps(DREAM_OUTPUT_SCHEMA)
