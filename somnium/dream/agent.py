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

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import SomniumConfig
from . import prompts
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


def _collect_existing_files(directory: Path, limit: int = 50) -> list[str]:
    if not directory.exists():
        return []
    files = sorted(p.name for p in directory.glob("*.md") if p.is_file())
    return files[:limit]


def _collect_skill_names(directory: Path, limit: int = 50) -> list[str]:
    if not directory.exists():
        return []
    names: list[str] = []
    for p in sorted(directory.iterdir()):
        if p.is_dir() and (p / "SKILL.md").exists():
            names.append(p.name)
    return names[:limit]


def build_prompt_for(transcript: Transcript, config: SomniumConfig) -> str:
    """Assemble the full user prompt for the dream agent."""
    project_root = str(config.project_root) if config.project_root else None

    global_mem = _collect_existing_files(config.global_memory_dir)
    project_mem = (
        _collect_existing_files(config.project_memory_dir)
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
        global_memory_files=global_mem,
        project_memory_files=project_mem,
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

    return _parse_output(proc.stdout, proc.stderr, proc.returncode)


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
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            pass

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
