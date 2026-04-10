"""Tests for the dream router (file dispatch)."""

from __future__ import annotations

from pathlib import Path

import pytest

from somnium import indexer
from somnium.config import SomniumConfig
from somnium.dream.router import (
    CLAUDE_MD_MARKER_END,
    CLAUDE_MD_MARKER_START,
    dispatch,
)
from somnium.embeddings.voyage import EmbedResult


class _FakeEmbedder:
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
    global_root = tmp_path / "home"
    (global_root / "memory").mkdir(parents=True)
    (global_root / "skills").mkdir(parents=True)

    project_root = tmp_path / "repo"
    (project_root / ".claude" / "somnium" / "memory").mkdir(parents=True)
    (project_root / ".git").mkdir(parents=True)

    cfg = SomniumConfig()
    cfg.storage.global_root = str(global_root)
    cfg.project_root = project_root

    fake = _FakeEmbedder()
    monkeypatch.setattr(indexer, "get_embedder", lambda c=None: fake)

    # Force embedding_dim=4 in the router's vector store creation path.
    import somnium.dream.router as router_module

    class _VS(router_module.VectorStore):
        def __init__(self, db_path, embedding_dim=4):
            super().__init__(db_path, embedding_dim=4)

    monkeypatch.setattr(router_module, "VectorStore", _VS)

    return cfg


def test_dispatch_global_memory(sandbox_cfg):
    items = [
        {
            "category": "global_memory",
            "title": "Graphite push",
            "content": "Always use `gt submit`.",
            "rationale": "user preference",
            "tags": ["git"],
        }
    ]
    records = dispatch(items, sandbox_cfg)
    assert len(records) == 1
    r = records[0]
    assert r.status == "written"
    target = Path(r.path)
    assert target.exists()
    assert target.is_relative_to(sandbox_cfg.global_memory_dir)
    # Filename is the slug only — no date prefix.
    assert target.name == "graphite-push.md"
    content = target.read_text()
    assert "Graphite push" in content
    assert "gt submit" in content
    assert "tags:" in content
    assert "updated_at:" in content


def test_dispatch_project_memory_without_project_is_skipped(tmp_path, monkeypatch):
    global_root = tmp_path / "home"
    (global_root / "memory").mkdir(parents=True)
    cfg = SomniumConfig()
    cfg.storage.global_root = str(global_root)
    cfg.project_root = None

    from somnium import indexer as idx_mod
    from somnium.embeddings.voyage import EmbedResult

    class FE:
        def embed(self, texts, **kw):
            return EmbedResult(embeddings=[[1, 0, 0, 0]] * len(texts), model="f", input_type="document")

        def embed_query(self, text, **kw):
            return [1, 0, 0, 0]

        def model_for(self, k):
            return "f"

    monkeypatch.setattr(idx_mod, "get_embedder", lambda c=None: FE())

    items = [
        {
            "category": "project_memory",
            "title": "a",
            "content": "b",
            "rationale": "c",
        }
    ]
    records = dispatch(items, cfg)
    assert records[0].status == "skipped"
    assert "project" in records[0].reason.lower()


def test_dispatch_global_skill(sandbox_cfg):
    items = [
        {
            "category": "global_skill",
            "title": "PR review flow",
            "content": "Steps:\n1. Read diff\n2. Check tests\n3. Approve",
            "rationale": "reusable",
        }
    ]
    records = dispatch(items, sandbox_cfg)
    assert records[0].status == "written"
    skill_path = Path(records[0].path)
    assert skill_path.name == "SKILL.md"
    assert skill_path.parent.parent == sandbox_cfg.global_skills_dir
    content = skill_path.read_text()
    assert "name: PR review flow" in content


def test_dispatch_claude_md_patch(sandbox_cfg):
    items = [
        {
            "category": "claude_md_patch",
            "title": "Graphite rule",
            "content": "- Always use `gt submit` instead of `git push`.",
            "rationale": "x",
        }
    ]
    records = dispatch(items, sandbox_cfg)
    assert records[0].status == "appended"
    claude_md = sandbox_cfg.project_root / "CLAUDE.md"
    assert claude_md.exists()
    text = claude_md.read_text()
    assert CLAUDE_MD_MARKER_START in text
    assert CLAUDE_MD_MARKER_END in text
    assert "gt submit" in text


def test_dispatch_unknown_category(sandbox_cfg):
    records = dispatch(
        [{"category": "foo", "title": "t", "content": "c", "rationale": "r"}],
        sandbox_cfg,
    )
    assert records[0].status == "skipped"


def test_dispatch_missing_fields(sandbox_cfg):
    records = dispatch(
        [{"category": "global_memory", "title": "", "content": "", "rationale": ""}],
        sandbox_cfg,
    )
    assert records[0].status == "skipped"
    assert "missing" in records[0].reason


def test_dispatch_same_title_overwrites_in_place(sandbox_cfg):
    """Two items with the same title must produce ONE file. The second
    write overwrites the first — that's the dedup contract."""
    item = {
        "category": "global_memory",
        "title": "Same title",
        "content": "first version",
        "rationale": "r",
    }
    records = dispatch([item, dict(item, content="second version")], sandbox_cfg)
    assert records[0].status == "written"
    assert records[1].status == "written"
    # Same path both times
    assert records[0].path == records[1].path
    # Only one file on disk
    files = list(sandbox_cfg.global_memory_dir.glob("*.md"))
    assert len(files) == 1
    # Second write won
    body = files[0].read_text()
    assert "second version" in body
    assert "first version" not in body


def test_dispatch_overwrite_preserves_created_at(sandbox_cfg):
    """When overwriting an existing memory, the original `created_at`
    must survive — only `updated_at` advances."""
    item = {
        "category": "global_memory",
        "title": "Persistent rule",
        "content": "v1",
        "rationale": "r",
    }
    records1 = dispatch([item], sandbox_cfg)
    first_path = Path(records1[0].path)
    first_text = first_path.read_text()
    first_created = next(
        line for line in first_text.splitlines() if line.startswith("created_at:")
    )

    # Sleep is unnecessary — just dispatch a second time with new content.
    dispatch([dict(item, content="v2")], sandbox_cfg)
    second_text = first_path.read_text()
    second_created = next(
        line for line in second_text.splitlines() if line.startswith("created_at:")
    )
    assert first_created == second_created
    assert "v2" in second_text
    assert "v1" not in second_text


def test_dispatch_claude_md_patch_appends_to_existing(sandbox_cfg):
    claude_md = sandbox_cfg.project_root / "CLAUDE.md"
    claude_md.write_text("# Project rules\n\n- Rule A\n", encoding="utf-8")

    dispatch(
        [
            {
                "category": "claude_md_patch",
                "title": "New rule",
                "content": "- Rule B",
                "rationale": "x",
            }
        ],
        sandbox_cfg,
    )
    text = claude_md.read_text()
    assert "Rule A" in text  # preserved
    assert "Rule B" in text
    assert CLAUDE_MD_MARKER_START in text
