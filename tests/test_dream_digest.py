"""Tests for the per-session dream digest writer."""

from __future__ import annotations

import pytest

from somnium.config import SomniumConfig
from somnium.dream.agent import DreamResult
from somnium.dream.digest import write_digest
from somnium.dream.gate import GateDecision, GateResult
from somnium.dream.router import WriteRecord
from somnium.dream.transcript import Transcript, Turn


@pytest.fixture
def cfg(tmp_path):
    c = SomniumConfig()
    c.storage.global_root = str(tmp_path / "home")
    return c


@pytest.fixture
def transcript():
    return Transcript(
        session_id="abc-12345678",
        cwd="/work/repo",
        turns=[
            Turn(role="user", text="hello"),
            Turn(role="assistant", text="hi"),
        ],
    )


# ---------------------------------------------------------------------------
# Skip path — gate said no
# ---------------------------------------------------------------------------


def test_skip_digest_is_minimal(cfg, transcript):
    gate = GateResult(
        decision=GateDecision.SKIP,
        reason="only 1 user message",
        category="trivial",
    )
    path = write_digest(
        config=cfg,
        transcript=transcript,
        gate=gate,
        dream=None,
        records=None,
    )
    assert path.exists()
    body = path.read_text()
    assert "skip" in body.lower()
    assert "Dream agent was not run" in body
    assert "only 1 user message" in body
    # No "Items written" section when skipped
    assert "Items written" not in body


def test_digest_filename_uses_session_id_prefix(cfg, transcript):
    gate = GateResult(decision=GateDecision.SKIP, reason="x")
    path = write_digest(
        config=cfg, transcript=transcript, gate=gate, dream=None, records=None
    )
    # Filename: <timestamp>-<session_id[:8]>.md
    assert path.name.endswith("abc-1234.md") or "abc-1234" in path.name


def test_digest_lands_under_dream_sessions(cfg, transcript):
    gate = GateResult(decision=GateDecision.SKIP, reason="x")
    path = write_digest(
        config=cfg, transcript=transcript, gate=gate, dream=None, records=None
    )
    assert path.parent == cfg.dream_dir / "sessions"


# ---------------------------------------------------------------------------
# Run path — agent ran with items
# ---------------------------------------------------------------------------


def test_run_digest_includes_dream_summary(cfg, transcript):
    gate = GateResult(
        decision=GateDecision.RUN,
        reason="3 user messages, 1 file write",
        category="implementation",
    )
    dream = DreamResult(
        should_persist=True,
        summary="Captured a Graphite preference and a React layout rule.",
        items=[
            {"category": "global_memory", "title": "Graphite"},
            {"category": "project_memory", "title": "React layout"},
        ],
        raw_stdout='{"structured_output": {}}',
    )
    records = [
        WriteRecord(
            category="global_memory",
            title="Graphite",
            path="/home/.claude/somnium/memory/graphite.md",
            status="written",
        ),
        WriteRecord(
            category="project_memory",
            title="React layout",
            path="/repo/.claude/somnium/memory/react-layout.md",
            status="written",
        ),
    ]
    path = write_digest(
        config=cfg, transcript=transcript, gate=gate, dream=dream, records=records
    )
    body = path.read_text()
    assert "should_persist: `True`" in body
    assert "items returned: 2" in body
    assert "Graphite" in body
    assert "React layout" in body
    assert "Items written" in body
    # Raw agent output collapsed in <details>
    assert "<details>" in body
    assert '"structured_output"' in body


def test_run_digest_table_includes_status_column(cfg, transcript):
    gate = GateResult(decision=GateDecision.RUN, reason="x", category="implementation")
    dream = DreamResult(should_persist=True, summary="ok", items=[])
    records = [
        WriteRecord("global_memory", "T1", "/p/t1.md", "written"),
        WriteRecord("project_skill", "T2", "/p/t2.md", "skipped", reason="no project"),
    ]
    path = write_digest(
        config=cfg, transcript=transcript, gate=gate, dream=dream, records=records
    )
    body = path.read_text()
    assert "| Status |" in body
    assert "| written |" in body
    assert "| skipped |" in body
    assert "no project" in body


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


def test_error_digest_includes_error_block(cfg, transcript):
    gate = GateResult(decision=GateDecision.RUN, reason="x", category="implementation")
    path = write_digest(
        config=cfg,
        transcript=transcript,
        gate=gate,
        dream=None,
        records=None,
        error="dream agent timed out after 300s",
    )
    body = path.read_text()
    assert "## Error" in body
    assert "timed out" in body
    # No agent summary because dream is None
    assert "Dream agent summary" not in body


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def test_digest_frontmatter_carries_session_metadata(cfg, transcript):
    gate = GateResult(decision=GateDecision.RUN, reason="x", category="implementation")
    path = write_digest(
        config=cfg, transcript=transcript, gate=gate, dream=None, records=None
    )
    body = path.read_text()
    head = body.split("---")[1]
    assert "session_id: abc-12345678" in head
    assert "cwd: /work/repo" in head
    assert "gate_decision: run" in head
    assert "category: implementation" in head
    assert "user_messages: 1" in head
