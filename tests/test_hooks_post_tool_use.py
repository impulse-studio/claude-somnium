"""Tests for the PostToolUse hook path routing and reindex logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from somnium import indexer
from somnium.config import SomniumConfig
from somnium.embeddings.voyage import EmbedResult
from somnium.hooks import post_tool_use
from somnium.hooks._common import classify_path
from somnium.storage.scope import Scope


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


@pytest.fixture
def sandbox_cfg(tmp_path: Path, monkeypatch):
    """Build a real SomniumConfig rooted in a tmp sandbox, and patch
    the embedder to avoid network calls. Dim=4."""
    global_root = tmp_path / "somnium_home"
    (global_root / "memory").mkdir(parents=True)
    (global_root / "skills").mkdir(parents=True)

    project_root = tmp_path / "myrepo"
    (project_root / ".claude" / "somnium" / "memory").mkdir(parents=True)
    (project_root / ".claude" / "skills").mkdir(parents=True)
    (project_root / ".git").mkdir(parents=True)

    cfg = SomniumConfig()
    cfg.storage.global_root = str(global_root)
    cfg.project_root = project_root

    fake = _FakeEmbedder()
    monkeypatch.setattr(indexer, "get_embedder", lambda c=None: fake)

    # Patch load_config inside the hook module to return our cfg.
    monkeypatch.setattr(
        post_tool_use,
        "load_config",
        lambda project_root=None: cfg,
    )
    # Force the dim on any ParquetStore created via the hook.
    from somnium.storage.parquet_store import ParquetStore as _RealPS

    monkeypatch.setattr(
        post_tool_use,
        "ParquetStore",
        lambda path: _RealPS(path, embedding_dim=4),
    )

    return cfg, tmp_path


# ---------------------------------------------------------------------------
# classify_path
# ---------------------------------------------------------------------------


def test_classify_global_memory(sandbox_cfg):
    cfg, _ = sandbox_cfg
    note = cfg.global_memory_dir / "n.md"
    note.write_text("# x")
    route = classify_path(note, cfg)
    assert route is not None
    assert route.scope == Scope.GLOBAL.value
    assert route.kind == "memory_global"


def test_classify_global_skill(sandbox_cfg):
    cfg, _ = sandbox_cfg
    skill_dir = cfg.global_skills_dir / "myskill"
    skill_dir.mkdir()
    skill = skill_dir / "SKILL.md"
    skill.write_text("# skill")
    route = classify_path(skill, cfg)
    assert route is not None
    assert route.scope == Scope.SKILL_GLOBAL.value
    assert route.kind == "skill_global"


def test_classify_project_memory(sandbox_cfg):
    cfg, _ = sandbox_cfg
    note = cfg.project_memory_dir / "p.md"
    note.write_text("# proj")
    route = classify_path(note, cfg)
    assert route is not None
    assert route.scope == Scope.PROJECT.value
    assert route.kind == "memory_project"


def test_classify_project_skill(sandbox_cfg):
    cfg, _ = sandbox_cfg
    skill = cfg.project_root / ".claude" / "skills" / "SKILL.md"
    skill.write_text("# ps")
    route = classify_path(skill, cfg)
    assert route is not None
    assert route.scope == Scope.SKILL_PROJECT.value
    assert route.kind == "skill_project"


def test_classify_out_of_scope(sandbox_cfg, tmp_path):
    cfg, _ = sandbox_cfg
    stray = tmp_path / "random.md"
    stray.write_text("# nope")
    route = classify_path(stray, cfg)
    assert route is None


# ---------------------------------------------------------------------------
# handle_event
# ---------------------------------------------------------------------------


def test_event_ignores_non_relevant_tools(sandbox_cfg):
    result = post_tool_use.handle_event({"tool_name": "Read", "tool_input": {}})
    assert "skipped" in result


def test_event_ignores_non_md_paths_out_of_scope(sandbox_cfg):
    cfg, _ = sandbox_cfg
    # A .py file under the global memory dir: not a .md (so not a memory)
    # and not inside a project code index either → out of scope.
    py_file = cfg.global_memory_dir / "x.py"
    py_file.write_text("print(1)")
    result = post_tool_use.handle_event(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(py_file)},
            "cwd": str(cfg.global_memory_dir),
        }
    )
    assert result["reindexed"][0]["skipped"] == "out of scope"


def test_event_reindexes_global_memory(sandbox_cfg):
    cfg, _ = sandbox_cfg
    note = cfg.global_memory_dir / "n.md"
    note.write_text("# Title\n\nSome body text that should be indexed.\n")
    result = post_tool_use.handle_event(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(note)},
            "cwd": str(cfg.global_memory_dir),
        }
    )
    assert "reindexed" in result
    entry = result["reindexed"][0]
    assert entry["scope"] == Scope.GLOBAL.value
    assert entry["chunks"] >= 1


def test_event_ignores_out_of_scope_file(sandbox_cfg, tmp_path):
    cfg, _ = sandbox_cfg
    stray = tmp_path / "loose.md"
    stray.write_text("# nope\n\nbody")
    result = post_tool_use.handle_event(
        {
            "tool_name": "Write",
            "tool_input": {"file_path": str(stray)},
            "cwd": str(tmp_path),
        }
    )
    assert result["reindexed"][0]["skipped"] == "out of scope"


def test_event_multiedit_with_multiple_edits(sandbox_cfg):
    cfg, _ = sandbox_cfg
    note = cfg.global_memory_dir / "m.md"
    note.write_text("# M\n\nbody")
    result = post_tool_use.handle_event(
        {
            "tool_name": "MultiEdit",
            "tool_input": {
                "file_path": str(note),
                "edits": [
                    {"old_string": "M", "new_string": "M2"},
                    {"old_string": "body", "new_string": "newbody"},
                ],
            },
            "cwd": str(cfg.global_memory_dir),
        }
    )
    # MultiEdit lists the same file_path multiple ways; we dedupe.
    assert len([r for r in result["reindexed"] if "chunks" in r]) == 1
