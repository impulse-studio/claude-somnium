"""Shared fixtures for end-to-end tests.

These tests call real APIs (Voyage + Claude). They are run by the
e2e.yml workflow, NOT by the blocking ci.yml. API keys come from
environment variables set in the workflow secrets.
"""

from __future__ import annotations

import os

import pytest


def _skip_if_missing(var: str) -> str:
    val = os.environ.get(var)
    if not val:
        pytest.skip(f"{var} not set — skipping live test")
    return val


@pytest.fixture
def voyage_key():
    return _skip_if_missing("VOYAGE_API_KEY")


@pytest.fixture
def claude_auth():
    """The dream agent calls `claude -p` which needs auth. Accept either
    CLAUDE_CODE_OAUTH_TOKEN (subscription) or ANTHROPIC_API_KEY (API key)."""
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    if not token:
        pytest.skip(
            "Neither CLAUDE_CODE_OAUTH_TOKEN nor ANTHROPIC_API_KEY is set — "
            "skipping live Claude test"
        )
    return token


@pytest.fixture
def somnium_sandbox(tmp_path, voyage_key, monkeypatch):
    """Build a fully isolated Somnium environment in a tmp dir.

    Returns (somnium_home, project_root).
    """
    somnium_home = tmp_path / "somnium_home"
    for sub in ("memory", "skills", "dream/sessions", "logs"):
        (somnium_home / sub).mkdir(parents=True)

    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    (project / ".claude" / "somnium" / "memory").mkdir(parents=True)

    # Write a minimal config with the real Voyage key
    config_path = somnium_home / "config.toml"
    config_path.write_text(
        f'[embeddings]\napi_key = "{voyage_key}"\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("SOMNIUM_HOME", str(somnium_home))

    # Reset cached config so the sandbox is picked up
    from somnium.config import reset_config_cache

    reset_config_cache()

    return somnium_home, project
