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

    # Force embedding_dim=4 in the router's ParquetStore creation path.
    import somnium.dream.router as router_module

    real_ps = router_module.ParquetStore

    class _PS(real_ps):
        def __init__(self, parquet_path, embedding_dim=4):
            super().__init__(parquet_path, embedding_dim=4)

    monkeypatch.setattr(router_module, "ParquetStore", _PS)

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


def test_dispatch_global_skill_is_rejected(sandbox_cfg):
    """global_skill is no longer supported. If the dream agent emits
    one anyway, the router skips it with a clear reason."""
    items = [
        {
            "category": "global_skill",
            "title": "PR review flow",
            "content": "Steps:\n1. Read diff\n2. Check tests\n3. Approve",
            "rationale": "reusable",
        }
    ]
    records = dispatch(items, sandbox_cfg)
    assert records[0].status == "skipped"
    assert "global_skill not supported" in records[0].reason


def test_dispatch_project_skill(sandbox_cfg):
    items = [
        {
            "category": "project_skill",
            "title": "Add API endpoint",
            "content": "Steps:\n1. Add route\n2. Register handler\n3. Run tests",
            "rationale": "reusable in this repo",
        }
    ]
    records = dispatch(items, sandbox_cfg)
    assert records[0].status == "written"
    skill_path = Path(records[0].path)
    assert skill_path.name == "SKILL.md"
    # Lives under <repo>/.claude/skills/<slug>/
    assert (
        skill_path.parent.parent
        == sandbox_cfg.project_root / ".claude" / "skills"
    )
    content = skill_path.read_text()
    assert "name: Add API endpoint" in content


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


# ---------------------------------------------------------------------------
# merge / delete actions
# ---------------------------------------------------------------------------


def _write_fake_memory(directory: Path, title: str, content: str) -> Path:
    """Write a minimal memory .md file for testing."""
    from somnium.dream.router import _slugify

    directory.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title)
    target = directory / f"{slug}.md"
    target.write_text(
        f"---\ncreated_at: 2026-01-01T00:00:00\ncategory: test\n---\n\n# {title}\n\n{content}\n",
        encoding="utf-8",
    )
    return target


def test_dispatch_delete_global_memory(sandbox_cfg):
    _write_fake_memory(sandbox_cfg.global_memory_dir, "Old fact", "outdated info")
    records = dispatch(
        [{"category": "global_memory", "title": "Old fact",
          "content": "obsolete", "rationale": "outdated", "action": "delete"}],
        sandbox_cfg,
    )
    assert any(r.status == "deleted" for r in records)
    assert not (sandbox_cfg.global_memory_dir / "old-fact.md").exists()


def test_dispatch_delete_nonexistent_is_skipped(sandbox_cfg):
    records = dispatch(
        [{"category": "global_memory", "title": "Does not exist",
          "content": "x", "rationale": "x", "action": "delete"}],
        sandbox_cfg,
    )
    assert records[0].status == "skipped"
    assert "not found" in records[0].reason


def test_dispatch_delete_project_memory(sandbox_cfg):
    _write_fake_memory(sandbox_cfg.project_memory_dir, "Stale note", "old")
    records = dispatch(
        [{"category": "project_memory", "title": "Stale note",
          "content": "x", "rationale": "x", "action": "delete"}],
        sandbox_cfg,
    )
    assert any(r.status == "deleted" for r in records)
    assert not (sandbox_cfg.project_memory_dir / "stale-note.md").exists()


def test_dispatch_delete_skill_is_rejected(sandbox_cfg):
    records = dispatch(
        [{"category": "project_skill", "title": "Some skill",
          "content": "x", "rationale": "x", "action": "delete"}],
        sandbox_cfg,
    )
    assert records[0].status == "skipped"
    assert "not supported" in records[0].reason


def test_dispatch_merge_consolidates_files(sandbox_cfg):
    mem_dir = sandbox_cfg.global_memory_dir
    _write_fake_memory(mem_dir, "Topic alpha", "alpha content")
    _write_fake_memory(mem_dir, "Topic beta", "beta content")
    _write_fake_memory(mem_dir, "Topic gamma", "gamma content")

    records = dispatch(
        [{
            "category": "global_memory",
            "action": "merge",
            "title": "Combined topic",
            "content": "alpha + beta + gamma consolidated",
            "rationale": "consolidation",
            "merge_sources": ["Topic alpha", "Topic beta", "Topic gamma"],
        }],
        sandbox_cfg,
    )

    merged = [r for r in records if r.status == "merged"]
    deleted = [r for r in records if r.status == "merge_source_deleted"]
    assert len(merged) == 1
    assert len(deleted) == 3

    # Merged file exists with new content
    merged_path = Path(merged[0].path)
    assert merged_path.exists()
    assert "consolidated" in merged_path.read_text()

    # Source files are gone
    assert not (mem_dir / "topic-alpha.md").exists()
    assert not (mem_dir / "topic-beta.md").exists()
    assert not (mem_dir / "topic-gamma.md").exists()


def test_dispatch_merge_preserves_self_reference(sandbox_cfg):
    """When a merge_source has the same slug as the merged title, don't delete it."""
    mem_dir = sandbox_cfg.global_memory_dir
    _write_fake_memory(mem_dir, "Keep this title", "old content")
    _write_fake_memory(mem_dir, "Other memory", "other content")

    records = dispatch(
        [{
            "category": "global_memory",
            "action": "merge",
            "title": "Keep this title",
            "content": "merged content",
            "rationale": "consolidation",
            "merge_sources": ["Keep this title", "Other memory"],
        }],
        sandbox_cfg,
    )

    merged = [r for r in records if r.status == "merged"]
    deleted = [r for r in records if r.status == "merge_source_deleted"]
    assert len(merged) == 1
    assert len(deleted) == 1  # only "Other memory" deleted, not self

    # Merged file has new content
    assert "merged content" in Path(merged[0].path).read_text()
    assert not (mem_dir / "other-memory.md").exists()


def test_dispatch_merge_missing_source_skips(sandbox_cfg):
    mem_dir = sandbox_cfg.global_memory_dir
    _write_fake_memory(mem_dir, "Real file", "real content")

    records = dispatch(
        [{
            "category": "global_memory",
            "action": "merge",
            "title": "Combined",
            "content": "merged",
            "rationale": "r",
            "merge_sources": ["Real file", "Ghost file"],
        }],
        sandbox_cfg,
    )

    merged = [r for r in records if r.status == "merged"]
    deleted = [r for r in records if r.status == "merge_source_deleted"]
    skipped = [r for r in records if r.status == "skipped"]
    assert len(merged) == 1
    assert len(deleted) == 1
    assert len(skipped) == 1
    assert "not found" in skipped[0].reason


def test_dispatch_merge_no_sources_is_skipped(sandbox_cfg):
    records = dispatch(
        [{
            "category": "global_memory",
            "action": "merge",
            "title": "X",
            "content": "x",
            "rationale": "r",
            "merge_sources": [],
        }],
        sandbox_cfg,
    )
    assert records[0].status == "skipped"
    assert "no merge_sources" in records[0].reason


def test_dispatch_merge_non_memory_rejected(sandbox_cfg):
    records = dispatch(
        [{
            "category": "project_skill",
            "action": "merge",
            "title": "X",
            "content": "x",
            "rationale": "r",
            "merge_sources": ["A", "B"],
        }],
        sandbox_cfg,
    )
    assert records[0].status == "skipped"
    assert "not supported" in records[0].reason


def test_dispatch_action_defaults_to_write(sandbox_cfg):
    """Items without an action field default to write (backwards compat)."""
    records = dispatch(
        [{"category": "global_memory", "title": "Plain write",
          "content": "no action field", "rationale": "r"}],
        sandbox_cfg,
    )
    assert records[0].status == "written"
    assert Path(records[0].path).exists()


def test_dispatch_merge_delete_limit_project(sandbox_cfg):
    """Project memories have a cap of 5 merge/delete per session."""
    mem_dir = sandbox_cfg.project_memory_dir
    for i in range(7):
        _write_fake_memory(mem_dir, f"Delete target {i}", f"content {i}")

    items = [
        {"category": "project_memory", "action": "delete",
         "title": f"Delete target {i}", "content": "x", "rationale": "x"}
        for i in range(7)
    ]
    records = dispatch(items, sandbox_cfg)

    deleted = [r for r in records if r.status == "deleted"]
    skipped = [r for r in records if r.status == "skipped"]
    assert len(deleted) == 5  # cap of 5
    assert len(skipped) == 2  # 6th and 7th
    assert all("limit" in r.reason for r in skipped)


def test_dispatch_global_memory_no_merge_delete_limit(sandbox_cfg):
    """Global memories have NO merge/delete limit."""
    mem_dir = sandbox_cfg.global_memory_dir
    for i in range(7):
        _write_fake_memory(mem_dir, f"Global target {i}", f"content {i}")

    items = [
        {"category": "global_memory", "action": "delete",
         "title": f"Global target {i}", "content": "x", "rationale": "x"}
        for i in range(7)
    ]
    records = dispatch(items, sandbox_cfg)

    deleted = [r for r in records if r.status == "deleted"]
    skipped = [r for r in records if r.status == "skipped"]
    assert len(deleted) == 7  # no cap for global
    assert len(skipped) == 0


def test_dispatch_merge_project_memory(sandbox_cfg):
    mem_dir = sandbox_cfg.project_memory_dir
    _write_fake_memory(mem_dir, "Proj note A", "content A")
    _write_fake_memory(mem_dir, "Proj note B", "content B")

    records = dispatch(
        [{
            "category": "project_memory",
            "action": "merge",
            "title": "Proj combined",
            "content": "A + B",
            "rationale": "consolidation",
            "merge_sources": ["Proj note A", "Proj note B"],
        }],
        sandbox_cfg,
    )
    merged = [r for r in records if r.status == "merged"]
    deleted = [r for r in records if r.status == "merge_source_deleted"]
    assert len(merged) == 1
    assert len(deleted) == 2


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
