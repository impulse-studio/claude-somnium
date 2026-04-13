"""Dream gate: decides whether a session is worth analyzing.

Hybrid strategy:
  1. Heuristic first (fast, deterministic).
  2. If the heuristic signal is ambiguous, fall back to an LLM judge
     (small/cheap model via ``claude -p``). The LLM judge is opt-in
     via ``dream.gate.llm_gate_enabled = true`` in config.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .agent import DREAM_SUBAGENT_ENV_VAR

if TYPE_CHECKING:
    from ..config import SomniumConfig
    from .transcript import Transcript


class GateDecision(Enum):
    RUN = "run"
    SKIP = "skip"


@dataclass
class GateResult:
    decision: GateDecision
    reason: str
    category: str | None = None  # e.g. "implementation", "discussion", "trivial"


def _matches_any(text: str, patterns: list[str]) -> bool:
    for pat in patterns:
        try:
            if re.search(pat, text.strip(), re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def decide(transcript: Transcript, config: SomniumConfig) -> GateResult:
    """Apply the heuristic gate."""
    gate_cfg = config.dream.gate

    # Rule 1: not enough user input → skip.
    if transcript.n_user_messages < gate_cfg.min_user_messages:
        return GateResult(
            decision=GateDecision.SKIP,
            reason=f"only {transcript.n_user_messages} user messages (min={gate_cfg.min_user_messages})",
            category="trivial",
        )

    user_texts = [t.text for t in transcript.real_user_turns]

    # Rule 2: every user message is a trivial command → skip.
    if user_texts and all(
        _matches_any(text, gate_cfg.skip_patterns) for text in user_texts
    ):
        return GateResult(
            decision=GateDecision.SKIP,
            reason="all user messages matched skip patterns",
            category="trivial",
        )

    # Rule 3: no file writes AND short session → probably a Q&A, skip.
    n_writes = len(transcript.file_writes)
    if n_writes == 0 and transcript.n_user_messages < 6:  # noqa: PLR2004
        return GateResult(
            decision=GateDecision.SKIP,
            reason="no file writes and short session (likely Q&A)",
            category="discussion",
        )

    # Otherwise: run. Categorize for the digest.
    category = "implementation" if n_writes > 0 else "discussion"

    return GateResult(
        decision=GateDecision.RUN,
        reason=(
            f"{transcript.n_user_messages} user messages, "
            f"{n_writes} file writes"
        ),
        category=category,
    )


# ------------------------------------------------------------------
# LLM judge — cheap Haiku pre-filter for ambiguous sessions
# ------------------------------------------------------------------

_LLM_GATE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["should_run", "reason"],
    "properties": {
        "should_run": {
            "type": "boolean",
            "description": (
                "True if the session contains actionable takeaways "
                "worth persisting to long-term memory."
            ),
        },
        "reason": {
            "type": "string",
            "description": "One sentence explaining the decision.",
        },
    },
}

_LLM_GATE_PROMPT = """\
You are a triage filter. A Claude Code session just ended. Below are the \
user's messages (assistant replies omitted). The session had NO file writes \
— it was pure discussion/Q&A.

Decide: does this session contain any actionable takeaway worth persisting \
as a long-term memory? Actionable means: a user correction, a preference \
statement, an architecture decision, a workflow choice, or a convention \
discovery. Pure information-seeking ("what does X do?", "how does Y work?") \
with no preference or decision expressed is NOT actionable.

Reply with JSON only.

## User messages

{user_messages}
"""


def _condense_for_judge(
    transcript: Transcript,
    *,
    max_turns: int = 15,
    max_chars: int = 500,
) -> str:
    """Extract user messages only, truncated, for the LLM judge."""
    lines: list[str] = []
    for turn in transcript.real_user_turns[:max_turns]:
        text = turn.text.strip()
        if len(text) > max_chars:
            text = text[:max_chars] + " [...]"
        lines.append(f"- {text}")
    if transcript.n_user_messages > max_turns:
        lines.append(
            f"- [{transcript.n_user_messages - max_turns} more messages omitted]"
        )
    return "\n".join(lines)


def llm_judge(transcript: Transcript, config: SomniumConfig) -> GateResult:
    """Call a cheap LLM to decide if a discussion session is worth dreaming.

    Only called for ambiguous sessions (category="discussion": 6+ user
    messages, no file writes). On ANY error, falls through to RUN so no
    dream is silently lost.
    """
    user_messages = _condense_for_judge(transcript)
    prompt = _LLM_GATE_PROMPT.format(user_messages=user_messages)
    schema = json.dumps(_LLM_GATE_SCHEMA)

    cmd = [
        "claude",
        "-p",
        prompt,
        "--model",
        config.dream.gate_model,
        "--output-format",
        "json",
        "--json-schema",
        schema,
        "--disable-slash-commands",
        "--max-budget-usd",
        "0.01",
        "--no-session-persistence",
    ]

    env = os.environ.copy()
    env[DREAM_SUBAGENT_ENV_VAR] = "1"

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return GateResult(
            decision=GateDecision.RUN,
            reason="llm_judge: subprocess error, falling through to RUN",
            category="discussion",
        )

    if proc.returncode != 0:
        return GateResult(
            decision=GateDecision.RUN,
            reason=f"llm_judge: exit code {proc.returncode}, falling through to RUN",
            category="discussion",
        )

    payload = _parse_judge_output(proc.stdout)
    if payload is None:
        return GateResult(
            decision=GateDecision.RUN,
            reason="llm_judge: parse error, falling through to RUN",
            category="discussion",
        )

    _log_judge_cost(proc.stdout, config)

    should_run = payload.get("should_run", True)
    reason = str(payload.get("reason", ""))

    decision = GateDecision.RUN if should_run else GateDecision.SKIP
    return GateResult(
        decision=decision,
        reason=f"llm_judge: {reason}",
        category="discussion",
    )


def _parse_judge_output(stdout: str) -> dict | None:
    """Parse the JSON envelope from ``claude -p --output-format json``."""
    stdout = stdout.strip()
    if not stdout:
        return None

    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError:
        return None

    if not isinstance(envelope, dict):
        return None

    payload = envelope.get("structured_output") or envelope.get("result")

    if isinstance(payload, str):
        with contextlib.suppress(json.JSONDecodeError):
            payload = json.loads(payload)

    return payload if isinstance(payload, dict) else None


def _log_judge_cost(stdout: str, config: SomniumConfig) -> None:
    """Extract cost from the ``claude -p`` envelope and log it."""
    try:
        envelope = json.loads(stdout.strip())
        if not isinstance(envelope, dict):
            return
        cost_usd = envelope.get("total_cost_usd", 0.0) or 0.0
        usage = envelope.get("usage", {}) or {}
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_tokens = input_tokens + output_tokens

        project_name = config.project_root.name if config.project_root else "global"

        from ..cost import log_cost

        log_cost(
            source="dream_gate",
            project=project_name,
            model=config.dream.gate_model,
            tokens=total_tokens,
            cost_usd=float(cost_usd),
            context=f"llm gate judge ({input_tokens} in, {output_tokens} out)",
        )
    except Exception:  # noqa: S110
        pass
