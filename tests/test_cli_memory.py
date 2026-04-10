"""Tests for the `somnium memory` subcommands."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from somnium.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def mem_sandbox(tmp_path, monkeypatch):
    """Isolated memory dirs with a few sample memories."""
    home = tmp_path / "home"
    somnium_home = home / ".claude" / "somnium"
    global_mem = somnium_home / "memory"
    global_mem.mkdir(parents=True)

    project = tmp_path / "repo"
    project_mem = project / ".claude" / "somnium" / "memory"
    project_mem.mkdir(parents=True)
    (project / ".git").mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("SOMNIUM_HOME", str(somnium_home))
    monkeypatch.chdir(project)

    from somnium.config import reset_config_cache

    reset_config_cache()

    # Seed global memories
    (global_mem / "graphite-push.md").write_text(
        "---\ntags: [\"git\"]\ncreated_at: 2026-04-01T00:00:00\n"
        "updated_at: 2026-04-02T00:00:00\n---\n\n"
        "# Graphite push\n\nAlways use gt submit.\n"
    )
    (global_mem / "uv-for-python.md").write_text(
        "---\ntags: [\"python\"]\ncreated_at: 2026-04-01T00:00:00\n---\n\n"
        "# UV for Python\n\nAlways use uv.\n"
    )
    # Seed project memory
    (project_mem / "react-layout.md").write_text(
        "---\ntags: [\"react\"]\ncreated_at: 2026-04-01T00:00:00\n---\n\n"
        "# React layout\n\nComponents in src/components/shared/.\n"
    )

    return {
        "global_mem": global_mem,
        "project_mem": project_mem,
        "project": project,
        "somnium_home": somnium_home,
    }


# ------------------------------------------------------------------
# list
# ------------------------------------------------------------------


def test_list_all_shows_both_scopes(runner, mem_sandbox):
    result = runner.invoke(app, ["memory", "list"])
    assert result.exit_code == 0
    assert "graphite-push" in result.stdout
    assert "uv-for-python" in result.stdout
    assert "react-layout" in result.stdout
    assert "3 memories total" in result.stdout


def test_list_scope_global_only(runner, mem_sandbox):
    result = runner.invoke(app, ["memory", "list", "--scope", "global"])
    assert result.exit_code == 0
    assert "graphite-push" in result.stdout
    assert "react-layout" not in result.stdout
    assert "2 memories total" in result.stdout


def test_list_scope_project_only(runner, mem_sandbox):
    result = runner.invoke(app, ["memory", "list", "--scope", "project"])
    assert result.exit_code == 0
    assert "react-layout" in result.stdout
    assert "graphite-push" not in result.stdout
    assert "1 memories total" in result.stdout


def test_list_empty_dir(runner, mem_sandbox):
    """If there are no memories at all, show a friendly message."""
    (mem_sandbox["global_mem"] / "graphite-push.md").unlink()
    (mem_sandbox["global_mem"] / "uv-for-python.md").unlink()
    (mem_sandbox["project_mem"] / "react-layout.md").unlink()

    result = runner.invoke(app, ["memory", "list"])
    assert result.exit_code == 0
    assert "No memories found" in result.stdout


def test_list_shows_scope_and_tags(runner, mem_sandbox):
    result = runner.invoke(app, ["memory", "list"])
    assert "global" in result.stdout
    assert "project" in result.stdout
    assert "git" in result.stdout
    assert "react" in result.stdout


def test_list_shows_updated_date(runner, mem_sandbox):
    result = runner.invoke(app, ["memory", "list"])
    assert "2026-04" in result.stdout


# ------------------------------------------------------------------
# show
# ------------------------------------------------------------------


def test_show_displays_content(runner, mem_sandbox):
    result = runner.invoke(app, ["memory", "show", "graphite-push"])
    assert result.exit_code == 0
    assert "Graphite push" in result.stdout
    assert "Always use gt submit" in result.stdout
    assert "(global)" in result.stdout


def test_show_project_memory(runner, mem_sandbox):
    result = runner.invoke(app, ["memory", "show", "react-layout"])
    assert result.exit_code == 0
    assert "React layout" in result.stdout
    assert "(project)" in result.stdout


def test_show_not_found(runner, mem_sandbox):
    result = runner.invoke(app, ["memory", "show", "does-not-exist"])
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


def test_show_with_scope_filter(runner, mem_sandbox):
    result = runner.invoke(
        app, ["memory", "show", "graphite-push", "--scope", "global"]
    )
    assert result.exit_code == 0
    assert "Graphite push" in result.stdout


# ------------------------------------------------------------------
# rm
# ------------------------------------------------------------------


def test_rm_with_yes_flag(runner, mem_sandbox):
    result = runner.invoke(app, ["memory", "rm", "uv-for-python", "--yes"])
    assert result.exit_code == 0
    assert "Deleted" in result.stdout
    assert not (mem_sandbox["global_mem"] / "uv-for-python.md").exists()


def test_rm_confirm_no_aborts(runner, mem_sandbox):
    result = runner.invoke(
        app, ["memory", "rm", "graphite-push"], input="n\n"
    )
    assert result.exit_code != 0  # typer.Abort
    assert (mem_sandbox["global_mem"] / "graphite-push.md").exists()


def test_rm_not_found(runner, mem_sandbox):
    result = runner.invoke(app, ["memory", "rm", "nope", "--yes"])
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


# ------------------------------------------------------------------
# move
# ------------------------------------------------------------------


def test_move_project_to_global(runner, mem_sandbox):
    result = runner.invoke(
        app, ["memory", "move", "react-layout", "--to", "global"]
    )
    assert result.exit_code == 0
    assert "project → global" in result.stdout
    assert (mem_sandbox["global_mem"] / "react-layout.md").exists()
    assert not (mem_sandbox["project_mem"] / "react-layout.md").exists()


def test_move_global_to_project(runner, mem_sandbox):
    result = runner.invoke(
        app, ["memory", "move", "graphite-push", "--to", "project"]
    )
    assert result.exit_code == 0
    assert "global → project" in result.stdout
    assert (mem_sandbox["project_mem"] / "graphite-push.md").exists()
    assert not (mem_sandbox["global_mem"] / "graphite-push.md").exists()


def test_move_already_in_scope(runner, mem_sandbox):
    result = runner.invoke(
        app, ["memory", "move", "graphite-push", "--to", "global"]
    )
    assert result.exit_code == 0
    assert "Already in global" in result.stdout


def test_move_not_found(runner, mem_sandbox):
    result = runner.invoke(
        app, ["memory", "move", "nope", "--to", "global"]
    )
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


# ------------------------------------------------------------------
# merge
# ------------------------------------------------------------------


def test_merge_two_memories(runner, mem_sandbox):
    result = runner.invoke(
        app,
        [
            "memory",
            "merge",
            "graphite-push",
            "uv-for-python",
            "--title",
            "Dev tools",
            "--yes",
        ],
    )
    assert result.exit_code == 0
    assert "Merged 2 memories" in result.stdout

    merged = mem_sandbox["global_mem"] / "dev-tools.md"
    assert merged.exists()
    body = merged.read_text()
    assert "gt submit" in body
    assert "uv" in body.lower()
    # Tags from both should be merged
    assert "git" in body
    assert "python" in body

    # Originals should be gone
    assert not (mem_sandbox["global_mem"] / "graphite-push.md").exists()
    assert not (mem_sandbox["global_mem"] / "uv-for-python.md").exists()


def test_merge_less_than_2_slugs(runner, mem_sandbox):
    result = runner.invoke(app, ["memory", "merge", "graphite-push", "--yes"])
    assert result.exit_code == 1
    assert "at least 2" in result.stdout.lower()


def test_merge_unknown_slug(runner, mem_sandbox):
    result = runner.invoke(
        app,
        ["memory", "merge", "graphite-push", "nonexistent", "--yes"],
    )
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


def test_merge_uses_first_slug_title_by_default(runner, mem_sandbox):
    result = runner.invoke(
        app,
        ["memory", "merge", "graphite-push", "uv-for-python", "--yes"],
    )
    assert result.exit_code == 0
    # Default title is from the first slug
    merged = mem_sandbox["global_mem"] / "graphite-push.md"
    assert merged.exists()
    body = merged.read_text()
    assert "# Graphite push" in body


def test_merge_cross_scope(runner, mem_sandbox):
    """Merge a global + project memory into a specific scope."""
    result = runner.invoke(
        app,
        [
            "memory",
            "merge",
            "graphite-push",
            "react-layout",
            "--title",
            "All conventions",
            "--scope",
            "project",
            "--yes",
        ],
    )
    assert result.exit_code == 0
    merged = mem_sandbox["project_mem"] / "all-conventions.md"
    assert merged.exists()
    body = merged.read_text()
    assert "gt submit" in body
    assert "components" in body.lower()
