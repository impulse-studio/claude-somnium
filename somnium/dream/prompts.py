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
"""


def build_user_prompt(
    *,
    transcript_markdown: str,
    project_root: str | None,
    global_memory_files: list[str],
    project_memory_files: list[str],
    existing_skills: list[str],
) -> str:
    """Render the user prompt that accompanies the transcript."""
    project_line = project_root or "(no project — general session)"

    def _fmt_list(items: list[str], max: int = 15) -> str:
        if not items:
            return "  (none)"
        shown = items[:max]
        out = "\n".join(f"  - {item}" for item in shown)
        if len(items) > max:
            out += f"\n  - … and {len(items) - max} more"
        return out

    return f"""\
# Session context

- Project root: {project_line}
- Existing global memories:
{_fmt_list(global_memory_files)}
- Existing project memories:
{_fmt_list(project_memory_files)}
- Existing skills:
{_fmt_list(existing_skills)}

# Transcript

{transcript_markdown}

# Task

Extract anything worth persisting. Respond with the JSON object only.
"""


def schema_json() -> str:
    return json.dumps(DREAM_OUTPUT_SCHEMA)
