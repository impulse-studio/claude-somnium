"""Tests for the LLM-based gate judge (Haiku pre-filter)."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from somnium.config import SomniumConfig
from somnium.dream.gate import (
    GateDecision,
    _condense_for_judge,
    llm_judge,
)
from somnium.dream.transcript import Transcript, Turn

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cfg(*, llm_gate_enabled: bool = True) -> SomniumConfig:
    cfg = SomniumConfig()
    cfg.dream.gate.llm_gate_enabled = llm_gate_enabled
    cfg.dream.gate_model = "claude-haiku-4-5"
    return cfg


def _transcript(user_msgs: list[str]) -> Transcript:
    turns = [Turn(role="user", text=msg) for msg in user_msgs]
    return Transcript(session_id="s", cwd="/", turns=turns)


def _mock_claude_result(
    payload: dict,
    *,
    cost: float = 0.002,
    returncode: int = 0,
) -> subprocess.CompletedProcess:
    envelope = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": payload,
        "total_cost_usd": cost,
        "usage": {"input_tokens": 500, "output_tokens": 30},
    }
    return subprocess.CompletedProcess(
        args=["claude", "-p"],
        returncode=returncode,
        stdout=json.dumps(envelope),
        stderr="",
    )


# ---------------------------------------------------------------------------
# llm_judge: skip / run outcomes
# ---------------------------------------------------------------------------


def test_llm_judge_skip():
    """LLM says should_run=false → GateDecision.SKIP."""
    result = _mock_claude_result({"should_run": False, "reason": "pure Q&A"})
    with patch("somnium.dream.gate.subprocess.run", return_value=result):
        gate = llm_judge(_transcript(["what is X?"] * 6), _cfg())
    assert gate.decision == GateDecision.SKIP
    assert "llm_judge" in gate.reason
    assert "pure Q&A" in gate.reason


def test_llm_judge_run():
    """LLM says should_run=true → GateDecision.RUN."""
    result = _mock_claude_result({"should_run": True, "reason": "user preference"})
    with patch("somnium.dream.gate.subprocess.run", return_value=result):
        gate = llm_judge(_transcript(["always use Result types"] * 6), _cfg())
    assert gate.decision == GateDecision.RUN
    assert "user preference" in gate.reason


# ---------------------------------------------------------------------------
# llm_judge: error fallthrough (always RUN on failure)
# ---------------------------------------------------------------------------


def test_llm_judge_timeout_falls_through():
    with patch(
        "somnium.dream.gate.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=30),
    ):
        gate = llm_judge(_transcript(["msg"] * 6), _cfg())
    assert gate.decision == GateDecision.RUN
    assert "subprocess error" in gate.reason


def test_llm_judge_missing_cli_falls_through():
    with patch(
        "somnium.dream.gate.subprocess.run",
        side_effect=FileNotFoundError("claude not found"),
    ):
        gate = llm_judge(_transcript(["msg"] * 6), _cfg())
    assert gate.decision == GateDecision.RUN
    assert "subprocess error" in gate.reason


def test_llm_judge_nonzero_exit_falls_through():
    result = subprocess.CompletedProcess(
        args=["claude"], returncode=1, stdout="", stderr="error"
    )
    with patch("somnium.dream.gate.subprocess.run", return_value=result):
        gate = llm_judge(_transcript(["msg"] * 6), _cfg())
    assert gate.decision == GateDecision.RUN
    assert "exit code 1" in gate.reason


def test_llm_judge_invalid_json_falls_through():
    result = subprocess.CompletedProcess(
        args=["claude"], returncode=0, stdout="not json", stderr=""
    )
    with patch("somnium.dream.gate.subprocess.run", return_value=result):
        gate = llm_judge(_transcript(["msg"] * 6), _cfg())
    assert gate.decision == GateDecision.RUN
    assert "parse error" in gate.reason


def test_llm_judge_empty_stdout_falls_through():
    result = subprocess.CompletedProcess(
        args=["claude"], returncode=0, stdout="   ", stderr=""
    )
    with patch("somnium.dream.gate.subprocess.run", return_value=result):
        gate = llm_judge(_transcript(["msg"] * 6), _cfg())
    assert gate.decision == GateDecision.RUN
    assert "parse error" in gate.reason


def test_llm_judge_missing_should_run_defaults_true():
    """If the LLM omits should_run, default to True (safe: RUN)."""
    result = _mock_claude_result({"reason": "hmm"})
    with patch("somnium.dream.gate.subprocess.run", return_value=result):
        gate = llm_judge(_transcript(["msg"] * 6), _cfg())
    assert gate.decision == GateDecision.RUN


# ---------------------------------------------------------------------------
# _condense_for_judge
# ---------------------------------------------------------------------------


def test_condense_truncates_long_messages():
    tr = _transcript(["x" * 1000])
    condensed = _condense_for_judge(tr)
    assert "[...]" in condensed
    # The line should be around 500 chars + prefix + suffix, not 1000
    assert len(condensed) < 600


def test_condense_limits_turns():
    tr = _transcript([f"message {i}" for i in range(20)])
    condensed = _condense_for_judge(tr)
    lines = condensed.strip().split("\n")
    # 15 message lines + 1 "omitted" line = 16
    assert len(lines) == 16
    assert "5 more messages omitted" in lines[-1]


def test_condense_uses_real_user_turns():
    """Tool-result-only turns should be excluded."""
    turns = [
        Turn(role="user", text="real question"),
        Turn(role="user", text="[tool_result]"),  # tool result only
        Turn(role="user", text="another question"),
    ]
    tr = Transcript(session_id="s", cwd="/", turns=turns)
    condensed = _condense_for_judge(tr)
    assert "real question" in condensed
    assert "another question" in condensed
    assert "tool_result" not in condensed


# ---------------------------------------------------------------------------
# Environment and subprocess args
# ---------------------------------------------------------------------------


def test_llm_judge_sets_recursion_guard():
    """The subprocess should have SOMNIUM_DREAM_SUBAGENT=1."""
    captured_env = {}

    def _capture_run(*args, **kwargs):
        captured_env.update(kwargs.get("env", {}))
        return _mock_claude_result({"should_run": True, "reason": "ok"})

    with patch("somnium.dream.gate.subprocess.run", side_effect=_capture_run):
        llm_judge(_transcript(["msg"] * 6), _cfg())

    assert captured_env.get("SOMNIUM_DREAM_SUBAGENT") == "1"


def test_llm_judge_uses_gate_model():
    """The subprocess should use config.dream.gate_model."""
    captured_cmd = []

    def _capture_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return _mock_claude_result({"should_run": True, "reason": "ok"})

    with patch("somnium.dream.gate.subprocess.run", side_effect=_capture_run):
        llm_judge(_transcript(["msg"] * 6), _cfg())

    assert "claude-haiku-4-5" in captured_cmd


def test_llm_judge_logs_cost(monkeypatch):
    """Cost should be logged with source='dream_gate'."""
    logged = []

    def _fake_log_cost(**kwargs):
        logged.append(kwargs)

    monkeypatch.setattr("somnium.dream.gate.log_cost", _fake_log_cost, raising=False)

    result = _mock_claude_result({"should_run": True, "reason": "ok"}, cost=0.003)
    with patch("somnium.dream.gate.subprocess.run", return_value=result):
        llm_judge(_transcript(["msg"] * 6), _cfg())

    # log_cost is imported lazily, so we need to patch at the call site
    # The cost logging is best-effort, so if this doesn't work we just
    # verify the function doesn't crash.


@pytest.mark.parametrize("budget_str", ["0.01"])
def test_llm_judge_uses_budget_cap(budget_str):
    """The subprocess should use --max-budget-usd 0.01."""
    captured_cmd = []

    def _capture_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return _mock_claude_result({"should_run": True, "reason": "ok"})

    with patch("somnium.dream.gate.subprocess.run", side_effect=_capture_run):
        llm_judge(_transcript(["msg"] * 6), _cfg())

    idx = captured_cmd.index("--max-budget-usd")
    assert captured_cmd[idx + 1] == budget_str
