"""E2E tests that hit the real Claude API via `claude -p`.

These verify the dream pipeline end-to-end: a real Claude sub-agent
reads a transcript, produces structured JSON, and the router writes
files. The `claude` CLI must be installed and ANTHROPIC_API_KEY set.

Cost: ~$0.10 per test (one Sonnet call via `claude -p --json-schema`).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from somnium.config import load_config
from somnium.dream.runner import run_dream


def _write_transcript(path: Path, cwd: str) -> None:
    """Write a realistic transcript with a convention worth persisting."""
    events = [
        {"type": "permission-mode", "sessionId": "e2e-dream-test", "permissionMode": "dontAsk"},
        {
            "type": "user",
            "sessionId": "e2e-dream-test",
            "cwd": cwd,
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "In this project we ALWAYS use named exports for "
                            "React components, never default exports. Please "
                            "remember this for future sessions."
                        ),
                    }
                ],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "Understood, I'll always use named exports.",
                    },
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": f"{cwd}/src/Counter.jsx"},
                    },
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Perfect, thanks."}],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Done!"}],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Also make sure all files have type annotations."}],
            },
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Added."},
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": f"{cwd}/src/Counter.jsx"},
                    },
                ],
            },
        },
    ]
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )


@pytest.fixture
def claude_available():
    if not shutil.which("claude"):
        pytest.skip("claude CLI not installed — skipping live dream test")


def test_dream_pipeline_with_real_claude(
    somnium_sandbox, anthropic_key, claude_available
):
    """Run the full dream pipeline with a real Claude sub-agent call.

    This is THE integration test: transcript → gate (force) → real
    claude -p with --json-schema → router writes files → verify.
    """
    somnium_home, project = somnium_sandbox
    config = load_config(project_root=project)

    # Write a transcript
    transcript_path = somnium_home / "test-transcript.jsonl"
    _write_transcript(transcript_path, str(project))

    # Run the dream pipeline, forcing the gate
    result = run_dream(
        transcript_path=transcript_path,
        config=config,
        force=True,
    )

    # Gate should say "forced"
    assert result.gate_result.decision.value == "run"
    assert "forced" in result.gate_result.reason

    # Dream agent should have run successfully
    assert result.dream_result is not None, f"Dream error: {result.error}"
    assert result.error is None

    # The agent should have returned at least 1 item
    dr = result.dream_result
    assert dr.summary, "Dream agent returned no summary"

    if dr.should_persist:
        # Router wrote files — verify at least one exists on disk
        assert len(result.write_records) >= 1
        for record in result.write_records:
            if record.status == "written":
                assert Path(record.path).exists(), f"Missing: {record.path}"
    else:
        # Agent decided nothing new to persist — still valid, the
        # transcript might not have had enough signal.
        pass

    # Digest should always be written
    assert result.digest_path is not None
    assert result.digest_path.exists()
    digest_body = result.digest_path.read_text()
    assert "Dream agent summary" in digest_body or "Dream digest" in digest_body


def test_dream_agent_structured_output_shape(
    somnium_sandbox, anthropic_key, claude_available
):
    """Verify that the dream agent's structured output matches the
    JSON schema we enforce via --json-schema."""
    somnium_home, project = somnium_sandbox
    config = load_config(project_root=project)

    transcript_path = somnium_home / "test-transcript-2.jsonl"
    _write_transcript(transcript_path, str(project))

    result = run_dream(
        transcript_path=transcript_path,
        config=config,
        force=True,
    )

    assert result.dream_result is not None
    dr = result.dream_result

    # should_persist is a bool
    assert isinstance(dr.should_persist, bool)
    # summary is a non-empty string
    assert isinstance(dr.summary, str)
    assert len(dr.summary) > 0
    # items is a list
    assert isinstance(dr.items, list)

    # Each item must have the required fields
    for item in dr.items:
        assert "category" in item
        assert "title" in item
        assert "content" in item
        assert "rationale" in item
        assert item["category"] in {
            "global_memory",
            "project_memory",
            "project_skill",
            "claude_md_patch",
        }, f"Unexpected category: {item['category']}"
