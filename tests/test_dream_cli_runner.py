"""Tests for the somnium-dream-run entry point."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from somnium.dream import cli_runner
from somnium.dream.agent import DreamResult
from somnium.dream.gate import GateDecision, GateResult
from somnium.dream.runner import DreamRunResult


def _make_transcript(tmp_path: Path) -> Path:
    """Minimal transcript JSONL file."""
    events = [
        {"type": "permission-mode", "sessionId": "cli-runner-test"},
        {
            "type": "user",
            "sessionId": "cli-runner-test",
            "cwd": str(tmp_path),
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "do a thing"}],
            },
        },
    ]
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return p


# ---------------------------------------------------------------------------
# Argument handling
# ---------------------------------------------------------------------------


def test_missing_transcript_file_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["somnium-dream-run", "--transcript", str(tmp_path / "nope.jsonl")]
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_runner.main()
    assert exc_info.value.code == 2


def test_missing_transcript_arg_exits_nonzero(monkeypatch):
    """argparse exits 2 on missing required arg."""
    monkeypatch.setattr("sys.argv", ["somnium-dream-run"])
    with pytest.raises(SystemExit) as exc_info:
        cli_runner.main()
    assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Pipeline invocation
# ---------------------------------------------------------------------------


def _stub_run_dream(should_persist=True, error=None):
    """Build a fake run_dream function returning a canned result."""

    def _fake(transcript_path, config, force=False):
        return DreamRunResult(
            gate_result=GateResult(
                decision=GateDecision.RUN, reason="forced" if force else "ok"
            ),
            transcript_path=transcript_path,
            dream_result=DreamResult(
                should_persist=should_persist, summary="ok", items=[]
            ),
            digest_path=Path("/tmp/fake-digest.md"),
            error=error,
        )

    return _fake


def test_main_calls_run_dream_on_valid_input(tmp_path, monkeypatch):
    transcript = _make_transcript(tmp_path)
    captured = {}

    def _fake_run_dream(*, transcript_path, config, force=False):
        captured["transcript_path"] = transcript_path
        captured["force"] = force
        return DreamRunResult(
            gate_result=GateResult(decision=GateDecision.RUN, reason="ok"),
            transcript_path=transcript_path,
            digest_path=tmp_path / "digest.md",
        )

    monkeypatch.setattr(cli_runner, "run_dream", _fake_run_dream)
    monkeypatch.setattr(
        "sys.argv",
        ["somnium-dream-run", "--transcript", str(transcript), "--cwd", str(tmp_path)],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_runner.main()
    assert exc_info.value.code == 0
    assert captured["transcript_path"] == transcript
    assert captured["force"] is False


def test_main_passes_force_flag(tmp_path, monkeypatch):
    transcript = _make_transcript(tmp_path)
    captured = {}

    def _fake_run_dream(*, transcript_path, config, force=False):
        captured["force"] = force
        return DreamRunResult(
            gate_result=GateResult(decision=GateDecision.RUN, reason="forced"),
            transcript_path=transcript_path,
            digest_path=tmp_path / "digest.md",
        )

    monkeypatch.setattr(cli_runner, "run_dream", _fake_run_dream)
    monkeypatch.setattr(
        "sys.argv",
        ["somnium-dream-run", "--transcript", str(transcript), "--force"],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_runner.main()
    assert exc_info.value.code == 0
    assert captured["force"] is True


def test_main_exits_1_on_dream_error(tmp_path, monkeypatch, capsys):
    transcript = _make_transcript(tmp_path)

    def _fake_run_dream(*, transcript_path, config, force=False):
        return DreamRunResult(
            gate_result=GateResult(decision=GateDecision.RUN, reason="ok"),
            transcript_path=transcript_path,
            digest_path=tmp_path / "digest.md",
            error="something went wrong",
        )

    monkeypatch.setattr(cli_runner, "run_dream", _fake_run_dream)
    monkeypatch.setattr(
        "sys.argv", ["somnium-dream-run", "--transcript", str(transcript)]
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_runner.main()
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "something went wrong" in err


def test_main_exits_1_on_unexpected_exception(tmp_path, monkeypatch):
    transcript = _make_transcript(tmp_path)

    def _crashing_run_dream(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli_runner, "run_dream", _crashing_run_dream)
    monkeypatch.setattr(
        "sys.argv", ["somnium-dream-run", "--transcript", str(transcript)]
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_runner.main()
    assert exc_info.value.code == 1
