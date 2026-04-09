"""Tests for the code indexer with a fake embedder."""

from __future__ import annotations

from pathlib import Path

import pytest

from somnium import indexer as memory_indexer_module
from somnium.code import indexer as code_indexer_module
from somnium.code.indexer import CODE_SCOPE, index_repo_code, index_single_code_file
from somnium.config import SomniumConfig
from somnium.embeddings.voyage import EmbedResult
from somnium.storage.vector import VectorStore


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
def sandbox(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "util.py").write_text(
        "def foo():\n    return 1\n", encoding="utf-8"
    )
    (repo / "src" / "view.tsx").write_text(
        "export const X = () => <div>hi</div>;\n", encoding="utf-8"
    )
    (repo / "README.txt").write_text("not code", encoding="utf-8")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "lib.js").write_text("x=1", encoding="utf-8")

    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "home")
    cfg.project_root = repo
    cfg.code_search.semantic_chunk_lines = 40

    monkeypatch.setattr(code_indexer_module, "get_embedder", lambda c=None: _FakeEmbedder())
    monkeypatch.setattr(memory_indexer_module, "get_embedder", lambda c=None: _FakeEmbedder())
    return cfg, repo


def test_index_repo_code(sandbox):
    cfg, repo = sandbox
    store = VectorStore(cfg.project_code_index_path, embedding_dim=4)
    stats = index_repo_code(root=repo, store=store, config=cfg)
    assert stats.files_seen == 2  # .py and .tsx; .txt ignored; node_modules pruned
    assert stats.files_embedded == 2
    assert stats.chunks_upserted >= 2
    assert store.stats()["files"] == 2
    store.close()


def test_index_single_code_file(sandbox):
    cfg, repo = sandbox
    store = VectorStore(cfg.project_code_index_path, embedding_dim=4)
    target = repo / "src" / "util.py"
    stats = index_single_code_file(store=store, path=target, config=cfg)
    assert stats.files_embedded == 1
    assert stats.chunks_upserted == 1
    store.close()


def test_hash_skip_on_second_run(sandbox):
    cfg, repo = sandbox
    store = VectorStore(cfg.project_code_index_path, embedding_dim=4)
    index_repo_code(root=repo, store=store, config=cfg)
    second = index_repo_code(root=repo, store=store, config=cfg)
    assert second.files_embedded == 0
    assert second.files_skipped == 2
    store.close()


def test_edit_triggers_reindex(sandbox):
    cfg, repo = sandbox
    store = VectorStore(cfg.project_code_index_path, embedding_dim=4)
    index_repo_code(root=repo, store=store, config=cfg)

    target = repo / "src" / "util.py"
    target.write_text("def foo():\n    return 42\n", encoding="utf-8")

    stats = index_repo_code(root=repo, store=store, config=cfg)
    assert stats.files_embedded == 1
    assert stats.files_skipped == 1
    store.close()


def test_deleted_file_is_pruned(sandbox):
    cfg, repo = sandbox
    store = VectorStore(cfg.project_code_index_path, embedding_dim=4)
    index_repo_code(root=repo, store=store, config=cfg)

    (repo / "src" / "view.tsx").unlink()
    stats = index_repo_code(root=repo, store=store, config=cfg)
    assert stats.files_deleted == 1
    assert store.stats()["files"] == 1
    store.close()


def test_post_tool_use_hook_reindexes_code(sandbox, monkeypatch):
    """The PostToolUse hook should reindex a source file under the project."""
    cfg, repo = sandbox
    # Bootstrap the code index so the hook has something to update.
    store = VectorStore(cfg.project_code_index_path, embedding_dim=4)
    index_repo_code(root=repo, store=store, config=cfg)
    store.close()

    from somnium.hooks import post_tool_use

    # Patch load_config to return our cfg. The hook's internal
    # find_project_root call will correctly detect the .git dir in our
    # tmp repo and update the cfg on its own, but since we pre-built
    # cfg here we just return it unchanged.
    monkeypatch.setattr(post_tool_use, "load_config", lambda project_root=None: cfg)

    real_vs = post_tool_use.VectorStore

    def _vs_dim4(path):
        return real_vs(path, embedding_dim=4)

    monkeypatch.setattr(post_tool_use, "VectorStore", _vs_dim4)

    # Edit a code file and fire the hook event.
    target = repo / "src" / "util.py"
    target.write_text("def foo():\n    return 99\n", encoding="utf-8")

    event = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(target)},
        "cwd": str(repo),
    }
    result = post_tool_use.handle_event(event)
    assert "reindexed" in result
    entry = result["reindexed"][0]
    assert entry["scope"] == "code"
    assert entry["chunks"] >= 1
