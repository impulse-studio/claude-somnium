"""Tests for the full dream pipeline orchestration in `dream/runner.py`.

The dream agent subprocess (`run_dream_agent`) is mocked end-to-end so
these tests run in milliseconds and never call Voyage or claude -p.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from somnium import indexer as memory_indexer
from somnium.config import SomniumConfig
from somnium.dream import runner as runner_module
from somnium.dream.agent import DreamAgentError, DreamResult
from somnium.dream.gate import GateDecision
from somnium.dream.runner import run_dream
from somnium.embeddings.voyage import EmbedResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    @property
    def embedding_dim(self):
        return 4

    def embed(self, texts, *, kind="text", input_type="document"):
        return EmbedResult(
            embeddings=[[1.0, 0.0, 0.0, 0.0] for _ in texts],
            model="fake",
            input_type=input_type,
        )

    def embed_query(self, text, *, kind="text"):
        return [1.0, 0.0, 0.0, 0.0]

    def model_for(self, kind):
        return "fake"


def _make_transcript_file(tmp_path: Path, n_user: int = 4, with_writes: bool = True) -> Path:
    """Build a JSONL transcript that passes the gate."""
    events: list[dict] = [{"type": "permission-mode", "sessionId": "test-session"}]
    for i in range(n_user):
        events.append(
            {
                "type": "user",
                "sessionId": "test-session",
                "cwd": str(tmp_path),
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": f"meaningful prompt {i}"}],
                },
            }
        )
        events.append(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "ok"},
                        *(
                            [{"type": "tool_use", "name": "Write", "input": {"file_path": str(tmp_path / "f.py")}}]
                            if with_writes and i == 0
                            else []
                        ),
                    ],
                },
            }
        )
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def sandbox_cfg(tmp_path, monkeypatch):
    global_root = tmp_path / "home"
    (global_root / "memory").mkdir(parents=True)
    (global_root / "skills").mkdir(parents=True)
    (global_root / "dream" / "sessions").mkdir(parents=True)

    project_root = tmp_path / "repo"
    (project_root / ".claude" / "somnium" / "memory").mkdir(parents=True)
    (project_root / ".git").mkdir(parents=True)

    cfg = SomniumConfig()
    cfg.storage.global_root = str(global_root)
    cfg.project_root = project_root

    monkeypatch.setattr(memory_indexer, "get_embedder", lambda c=None: _FakeEmbedder())

    # Force the router to use dim=4 for the test stores.
    import somnium.dream.router as router_module

    real_ps = router_module.ParquetStore

    def _ps_dim4(path, embedding_dim=4):
        return real_ps(path, embedding_dim=4)

    monkeypatch.setattr(router_module, "ParquetStore", _ps_dim4)
    return cfg


# ---------------------------------------------------------------------------
# Skip path — the gate refuses
# ---------------------------------------------------------------------------


def test_run_dream_skips_short_session(sandbox_cfg, tmp_path, monkeypatch):
    """A 1-user-message transcript fails the gate → no agent call, digest written."""
    transcript = _make_transcript_file(tmp_path, n_user=1, with_writes=False)

    called = {"count": 0}

    def _fake_run_agent(*args, **kwargs):
        called["count"] += 1
        return DreamResult(should_persist=False, summary="", items=[])

    monkeypatch.setattr(runner_module, "run_dream_agent", _fake_run_agent)

    result = run_dream(transcript_path=transcript, config=sandbox_cfg)
    assert result.gate_result.decision == GateDecision.SKIP
    assert called["count"] == 0
    assert result.dream_result is None
    assert result.digest_path is not None
    assert result.digest_path.exists()


def test_run_dream_force_bypasses_gate(sandbox_cfg, tmp_path, monkeypatch):
    """force=True must run the agent even on a session that would normally skip."""
    transcript = _make_transcript_file(tmp_path, n_user=1, with_writes=False)

    def _fake_run_agent(*args, **kwargs):
        return DreamResult(
            should_persist=True,
            summary="forced",
            items=[
                {
                    "category": "global_memory",
                    "title": "Forced item",
                    "content": "body",
                    "rationale": "r",
                }
            ],
        )

    monkeypatch.setattr(runner_module, "run_dream_agent", _fake_run_agent)

    result = run_dream(transcript_path=transcript, config=sandbox_cfg, force=True)
    assert result.gate_result.decision == GateDecision.RUN
    assert result.gate_result.reason == "forced via manual trigger"
    assert result.ran_agent
    assert len(result.write_records) == 1
    assert result.write_records[0].status == "written"


# ---------------------------------------------------------------------------
# Run path — gate passes, agent returns items
# ---------------------------------------------------------------------------


def test_run_dream_dispatches_items(sandbox_cfg, tmp_path, monkeypatch):
    transcript = _make_transcript_file(tmp_path, n_user=4, with_writes=True)

    def _fake_run_agent(*args, **kwargs):
        return DreamResult(
            should_persist=True,
            summary="ok",
            items=[
                {
                    "category": "global_memory",
                    "title": "Some rule",
                    "content": "do this",
                    "rationale": "user said so",
                },
            ],
        )

    monkeypatch.setattr(runner_module, "run_dream_agent", _fake_run_agent)

    result = run_dream(transcript_path=transcript, config=sandbox_cfg)
    assert result.ran_agent
    assert result.gate_result.decision == GateDecision.RUN
    assert len(result.write_records) == 1
    record = result.write_records[0]
    assert record.status == "written"
    assert Path(record.path).exists()
    assert Path(record.path).name == "some-rule.md"


def test_run_dream_should_not_persist_writes_no_items(sandbox_cfg, tmp_path, monkeypatch):
    """Agent returns should_persist=False → router is not called."""
    transcript = _make_transcript_file(tmp_path, n_user=4, with_writes=True)

    def _fake_run_agent(*args, **kwargs):
        return DreamResult(
            should_persist=False,
            summary="nothing worth keeping",
            items=[],
        )

    monkeypatch.setattr(runner_module, "run_dream_agent", _fake_run_agent)

    result = run_dream(transcript_path=transcript, config=sandbox_cfg)
    assert result.ran_agent
    assert result.write_records == []
    # Digest still gets written so we have a trace
    assert result.digest_path.exists()


# ---------------------------------------------------------------------------
# Error path — agent crashes
# ---------------------------------------------------------------------------


def test_run_dream_agent_error_writes_error_digest(sandbox_cfg, tmp_path, monkeypatch):
    transcript = _make_transcript_file(tmp_path, n_user=4, with_writes=True)

    def _fake_run_agent(*args, **kwargs):
        raise DreamAgentError("simulated timeout")

    monkeypatch.setattr(runner_module, "run_dream_agent", _fake_run_agent)

    result = run_dream(transcript_path=transcript, config=sandbox_cfg)
    assert result.ran_agent is False
    assert result.error == "simulated timeout"
    assert result.digest_path.exists()
    body = result.digest_path.read_text()
    assert "simulated timeout" in body
