"""Dream gate: decides whether a session is worth analyzing.

Hybrid strategy:
  1. Heuristic first (fast, deterministic).
  2. If the heuristic signal is ambiguous, fall back to an LLM judge
     (small/cheap model via `claude -p`). Phase 3 ships only the
     heuristic; the LLM judge slot is wired but disabled by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

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
    if n_writes == 0 and transcript.n_user_messages < 6:
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
