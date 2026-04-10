"""Tests for the dream agent's `_parse_output` JSON envelope handling.

We don't test the subprocess spawn (that requires real `claude -p`),
but we exhaustively test the envelope parser because it's the seam
between the LLM and the rest of Somnium and breaks subtly.
"""

from __future__ import annotations

import json

import pytest

from somnium.dream.agent import DreamAgentError, _parse_output


def _envelope(structured_output: dict) -> str:
    """Build a Claude Code print-mode envelope with structured_output."""
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "",
            "structured_output": structured_output,
            "duration_ms": 1234,
        }
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_parses_structured_output_field():
    payload = {
        "should_persist": True,
        "summary": "ok",
        "items": [
            {
                "category": "global_memory",
                "title": "Foo",
                "content": "bar",
                "rationale": "baz",
            }
        ],
    }
    result = _parse_output(_envelope(payload), stderr="", exit_code=0)
    assert result.should_persist is True
    assert result.summary == "ok"
    assert len(result.items) == 1
    assert result.items[0]["title"] == "Foo"
    assert result.exit_code == 0


def test_parses_empty_items_list():
    payload = {"should_persist": False, "summary": "nothing worth keeping", "items": []}
    result = _parse_output(_envelope(payload), stderr="", exit_code=0)
    assert result.should_persist is False
    assert result.items == []


def test_falls_back_to_result_field():
    """Older envelopes (or fallback path) put the JSON in `result` as
    a string. We must double-decode it."""
    payload = {"should_persist": True, "summary": "x", "items": []}
    envelope = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": json.dumps(payload),  # string-encoded JSON
        }
    )
    result = _parse_output(envelope, stderr="", exit_code=0)
    assert result.should_persist is True


def test_falls_back_to_content_field():
    payload = {"should_persist": True, "summary": "x", "items": []}
    envelope = json.dumps({"content": payload})
    result = _parse_output(envelope, stderr="", exit_code=0)
    assert result.should_persist is True


def test_preserves_raw_stdout_and_stderr():
    payload = {"should_persist": True, "summary": "x", "items": []}
    raw = _envelope(payload)
    result = _parse_output(raw, stderr="a warning", exit_code=0)
    assert result.raw_stdout == raw.strip()
    assert result.raw_stderr == "a warning"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_empty_stdout_raises():
    with pytest.raises(DreamAgentError, match="empty stdout"):
        _parse_output("", stderr="", exit_code=0)


def test_whitespace_only_stdout_raises():
    with pytest.raises(DreamAgentError, match="empty stdout"):
        _parse_output("   \n   ", stderr="", exit_code=0)


def test_invalid_json_raises():
    with pytest.raises(DreamAgentError, match="not valid JSON"):
        _parse_output("not json at all", stderr="", exit_code=0)


def test_envelope_is_error_raises():
    envelope = json.dumps(
        {
            "type": "result",
            "subtype": "error",
            "is_error": True,
            "result": "Not logged in · Please run /login",
        }
    )
    with pytest.raises(DreamAgentError, match="Not logged in"):
        _parse_output(envelope, stderr="", exit_code=0)


def test_payload_not_an_object_raises():
    """The structured_output must be a JSON object, not an array or scalar."""
    envelope = json.dumps({"structured_output": ["not", "an", "object"]})
    with pytest.raises(DreamAgentError, match="not an object"):
        _parse_output(envelope, stderr="", exit_code=0)


def test_string_payload_that_isnt_json_raises():
    """If `result` is a non-JSON string, we can't fall back further."""
    envelope = json.dumps({"result": "just a sentence"})
    with pytest.raises(DreamAgentError, match="not an object"):
        _parse_output(envelope, stderr="", exit_code=0)


def test_missing_should_persist_defaults_to_false():
    """A malformed-but-parseable payload shouldn't crash — should_persist
    defaults to False so the router won't dispatch anything."""
    envelope = json.dumps({"structured_output": {"summary": "x", "items": []}})
    result = _parse_output(envelope, stderr="", exit_code=0)
    assert result.should_persist is False
