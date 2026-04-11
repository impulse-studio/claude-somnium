"""Dream agent: spawns `claude -p` with structured output to analyze
a session and return classification JSON.

Design:
  - Uses `--bare` to skip hooks/CLAUDE.md/plugins — prevents recursive
    Stop-hook invocation (a dream run would otherwise trigger itself).
  - Uses `--output-format json` and `--json-schema` so we get validated
    structured output back.
  - Uses `--disable-slash-commands` and explicit `--tools ""` to make
    the sub-agent a pure text generator with no tool access.
  - Budget-capped via `--max-budget-usd`.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import prompts

if TYPE_CHECKING:
    from pathlib import Path

    from ..config import SomniumConfig
    from .transcript import Transcript

# Env var set on the sub-agent subprocess so the Somnium Stop hook
# detects it and skips — prevents recursive dream loops.
DREAM_SUBAGENT_ENV_VAR = "SOMNIUM_DREAM_SUBAGENT"


class DreamAgentError(Exception):
    """Raised when the dream agent subprocess fails or returns garbage."""


@dataclass
class DreamResult:
    should_persist: bool
    summary: str
    items: list[dict[str, Any]] = field(default_factory=list)
    raw_stdout: str = ""
    raw_stderr: str = ""
    exit_code: int = 0


def _collect_existing_titles(directory: Path, limit: int = 50) -> list[str]:
    """Return the human-readable title of each .md memory in `directory`.

    Title resolution order:
      1. The first H1 (`# Title`) in the file body
      2. The `title` field in YAML frontmatter
      3. The slugified filename, as a last resort

    Falling back to titles (rather than filenames) gives the dream
    agent something it can match character-for-character when deciding
    whether to update an existing memory rather than create a duplicate.
    """
    import re

    import frontmatter as fm

    if not directory.exists():
        return []

    titles: list[str] = []
    for path in sorted(directory.glob("*.md")):
        if not path.is_file():
            continue
        try:
            post = fm.loads(path.read_text(encoding="utf-8"))
        except Exception:
            titles.append(path.stem)
            continue

        h1 = re.search(r"^#\s+(.+?)\s*$", post.content or "", re.MULTILINE)
        if h1:
            titles.append(h1.group(1).strip())
        elif post.metadata.get("title"):
            titles.append(str(post.metadata["title"]))
        else:
            titles.append(path.stem)

    return titles[:limit]


def _collect_skill_names(directory: Path, limit: int = 50) -> list[str]:
    if not directory.exists():
        return []
    names: list[str] = [
        p.name
        for p in sorted(directory.iterdir())
        if p.is_dir() and (p / "SKILL.md").exists()
    ]
    return names[:limit]


def build_prompt_for(transcript: Transcript, config: SomniumConfig) -> str:
    """Assemble the full user prompt for the dream agent."""
    project_root = str(config.project_root) if config.project_root else None

    global_mem = _collect_existing_titles(config.global_memory_dir)
    project_mem = (
        _collect_existing_titles(config.project_memory_dir)
        if config.project_memory_dir
        else []
    )
    skills: list[str] = []
    skills.extend(_collect_skill_names(config.global_skills_dir))
    if config.project_root:
        skills.extend(_collect_skill_names(config.project_root / ".claude" / "skills"))

    return prompts.build_user_prompt(
        transcript_markdown=transcript.as_markdown(),
        project_root=project_root,
        global_memory_titles=global_mem,
        project_memory_titles=project_mem,
        existing_skills=skills,
    )


def run_dream_agent(
    transcript: Transcript,
    config: SomniumConfig,
    *,
    timeout_seconds: int = 300,
    max_budget_usd: float = 0.50,
) -> DreamResult:
    """Invoke `claude -p` with the dream prompt and return parsed JSON."""
    user_prompt = build_prompt_for(transcript, config)
    schema = prompts.schema_json()

    cmd = [
        "claude",
        "-p",
        user_prompt,
        "--model",
        config.dream.model,
        "--append-system-prompt",
        prompts.DREAM_SYSTEM_PROMPT,
        "--output-format",
        "json",
        "--json-schema",
        schema,
        "--disable-slash-commands",
        "--max-budget-usd",
        str(max_budget_usd),
        "--no-session-persistence",
    ]

    # Pass the subagent marker via env so our Stop hook detects it and
    # no-ops. We do NOT use --bare because that disables OAuth/keychain
    # and breaks subscription-only users.
    env = os.environ.copy()
    env[DREAM_SUBAGENT_ENV_VAR] = "1"

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        raise DreamAgentError(
            "`claude` CLI not found on PATH. Is Claude Code installed?"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise DreamAgentError(
            f"dream agent timed out after {timeout_seconds}s"
        ) from exc

    if proc.returncode != 0:
        raise DreamAgentError(
            f"dream agent exited {proc.returncode}: {proc.stderr[:500]}"
        )

    result = _parse_output(proc.stdout, proc.stderr, proc.returncode)

    # Log dream cost from the envelope
    project_name = config.project_root.name if config.project_root else "global"
    _log_dream_cost(proc.stdout, config.dream.model, project_name)

    return result


def _parse_output(stdout: str, stderr: str, exit_code: int) -> DreamResult:
    """Parse the JSON output returned by `claude -p --output-format json`.

    When `--json-schema` is used, the validated payload lives at
    envelope.structured_output. We fall back to envelope.result for
    compatibility if structured_output is missing.
    """
    stdout = stdout.strip()
    if not stdout:
        raise DreamAgentError("dream agent returned empty stdout")

    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise DreamAgentError(
            f"dream agent stdout was not valid JSON: {exc}"
        ) from exc

    if isinstance(envelope, dict) and envelope.get("is_error"):
        result_text = envelope.get("result") or envelope.get("error") or ""
        raise DreamAgentError(
            f"dream agent reported error: {str(result_text)[:500]}"
        )

    payload: Any = None
    if isinstance(envelope, dict):
        # Preferred: structured_output field (populated by --json-schema)
        if "structured_output" in envelope:
            payload = envelope["structured_output"]
        elif "result" in envelope:
            payload = envelope["result"]
        elif "content" in envelope:
            payload = envelope["content"]

    # The payload might be a string-encoded JSON object — double-decode.
    if isinstance(payload, str):
        with contextlib.suppress(json.JSONDecodeError):
            payload = json.loads(payload)

    if not isinstance(payload, dict):
        raise DreamAgentError(
            f"dream agent payload is not an object: {str(payload)[:200]}"
        )

    return DreamResult(
        should_persist=bool(payload.get("should_persist", False)),
        summary=str(payload.get("summary", "")),
        items=list(payload.get("items", []) or []),
        raw_stdout=stdout,
        raw_stderr=stderr,
        exit_code=exit_code,
    )


def _log_dream_cost(stdout: str, model: str, project: str = "global") -> None:
    """Extract cost from the claude -p JSON envelope and log it."""
    try:
        envelope = json.loads(stdout.strip())
        if not isinstance(envelope, dict):
            return
        cost_usd = envelope.get("total_cost_usd", 0.0) or 0.0
        usage = envelope.get("usage", {}) or {}
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        total_tokens = input_tokens + output_tokens + cache_read

        from ..cost import log_cost

        log_cost(
            source="dream",
            project=project,
            model=model,
            tokens=total_tokens,
            cost_usd=float(cost_usd),
            context=f"dream run ({input_tokens} in, {output_tokens} out)",
            extra={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
            },
        )
    except Exception:  # noqa: S110
        pass
