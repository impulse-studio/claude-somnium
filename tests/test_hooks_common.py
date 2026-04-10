"""Tests for the shared hook helpers in `somnium/hooks/_common.py`."""

from __future__ import annotations

import pytest

from somnium.config import SomniumConfig
from somnium.hooks import _common
from somnium.hooks._common import log_error, log_info, read_event

# ---------------------------------------------------------------------------
# read_event
# ---------------------------------------------------------------------------


class _StdinStub:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text


def test_read_event_returns_dict_for_valid_json(monkeypatch):
    monkeypatch.setattr("sys.stdin", _StdinStub('{"tool_name": "Write"}'))
    event = read_event()
    assert event == {"tool_name": "Write"}


def test_read_event_returns_empty_for_blank_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", _StdinStub(""))
    assert read_event() == {}


def test_read_event_returns_empty_for_invalid_json(monkeypatch):
    """Hooks must degrade gracefully — never raise on bad input."""
    monkeypatch.setattr("sys.stdin", _StdinStub("not json"))
    assert read_event() == {}


def test_read_event_returns_empty_for_unexpected_type(monkeypatch):
    """A JSON array isn't a valid hook event but shouldn't crash."""
    monkeypatch.setattr("sys.stdin", _StdinStub('["array", "not", "object"]'))
    # Function returns whatever json.loads gives — caller must validate.
    # The contract is "no exceptions" not "always a dict".
    result = read_event()
    assert result == ["array", "not", "object"]


# ---------------------------------------------------------------------------
# log_info / log_error
# ---------------------------------------------------------------------------


@pytest.fixture
def log_sandbox(tmp_path, monkeypatch):
    """Redirect the global root so log files land in tmp_path."""
    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "home")
    monkeypatch.setattr(_common, "load_config", lambda: cfg)
    return cfg


def test_log_info_appends_to_hooks_log(log_sandbox):
    log_info("test_hook", "first message")
    log_info("test_hook", "second message")

    log_path = log_sandbox.global_root / "logs" / "hooks.log"
    assert log_path.exists()
    body = log_path.read_text()
    assert "first message" in body
    assert "second message" in body
    # Each entry has the hook name
    assert body.count("test_hook") >= 2


def test_log_error_writes_traceback(log_sandbox):
    try:
        raise ValueError("intentional test error")
    except ValueError as exc:
        log_error("test_hook", exc)

    log_path = log_sandbox.global_root / "logs" / "hooks.log"
    assert log_path.exists()
    body = log_path.read_text()
    assert "intentional test error" in body
    assert "Traceback" in body


def test_log_info_creates_logs_dir(log_sandbox):
    """The logs directory is auto-created on first write."""
    log_dir = log_sandbox.global_root / "logs"
    assert not log_dir.exists()
    log_info("hook", "message")
    assert log_dir.exists()


def test_log_helpers_swallow_their_own_errors(monkeypatch):
    """If logging itself fails (e.g. read-only filesystem), the hook
    must not crash. The helpers swallow internal exceptions."""
    monkeypatch.setattr(
        _common,
        "load_config",
        lambda: (_ for _ in ()).throw(RuntimeError("config broken")),
    )
    # Should not raise
    log_info("hook", "message")
    log_error("hook", RuntimeError("inner"))
