"""Tests for the dream gate (heuristic decisioning)."""

from __future__ import annotations

from somnium.config import SomniumConfig
from somnium.dream.gate import GateDecision, decide
from somnium.dream.transcript import Transcript, Turn


def _cfg(min_messages: int = 3, skip_patterns=None) -> SomniumConfig:
    cfg = SomniumConfig()
    cfg.dream.gate.min_user_messages = min_messages
    cfg.dream.gate.skip_patterns = skip_patterns or [
        "^commit( this)?$",
        "^push( this)?$",
        "^run tests?$",
    ]
    return cfg


def _t(users: list[str], writes: list[str] | None = None) -> Transcript:
    turns: list[Turn] = []
    for u in users:
        turns.append(Turn(role="user", text=u))
    if writes:
        turns.append(
            Turn(
                role="assistant",
                text="done",
                tool_uses=[
                    {"name": "Write", "input": {"file_path": w}} for w in writes
                ],
            )
        )
    return Transcript(session_id="s", cwd="/", turns=turns)


def test_skip_too_short():
    tr = _t(["hello"])
    result = decide(tr, _cfg(min_messages=3))
    assert result.decision == GateDecision.SKIP
    assert "user messages" in result.reason


def test_skip_all_trivial_patterns():
    tr = _t(["commit this", "push", "run tests"])
    result = decide(tr, _cfg(min_messages=2))
    assert result.decision == GateDecision.SKIP
    assert "skip patterns" in result.reason


def test_skip_short_qa_no_writes():
    tr = _t(["what is X?", "and how does Y work?", "got it"])
    result = decide(tr, _cfg(min_messages=3))
    assert result.decision == GateDecision.SKIP
    assert "Q&A" in result.reason


def test_run_long_qa_even_without_writes():
    tr = _t(
        [
            "what is X",
            "and Y",
            "also Z",
            "and W",
            "and finally Q",
            "thanks",
        ]
    )
    result = decide(tr, _cfg(min_messages=3))
    assert result.decision == GateDecision.RUN
    assert result.category == "discussion"


def test_run_with_writes():
    tr = _t(
        [
            "implement feature X",
            "add tests for it",
            "looks good",
        ],
        writes=["/repo/feat.py", "/repo/test.py"],
    )
    result = decide(tr, _cfg(min_messages=3))
    assert result.decision == GateDecision.RUN
    assert result.category == "implementation"


def test_single_trivial_plus_real_does_not_skip():
    tr = _t(
        [
            "commit this",
            "but first, can you make sure we always prefer gt submit over git push in this project?",
            "ok thanks",
        ],
        writes=["/repo/CLAUDE.md"],
    )
    result = decide(tr, _cfg(min_messages=3))
    # Not ALL messages match skip patterns → run
    assert result.decision == GateDecision.RUN
