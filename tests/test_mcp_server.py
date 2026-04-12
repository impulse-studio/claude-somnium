"""Tests for the MCP server tool functions.

These test the bare Python functions decorated with @mcp.tool() —
the FastMCP transport layer (stdio, JSON-RPC framing) is the SDK's
responsibility, not ours.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from somnium import indexer as memory_indexer
from somnium.config import SomniumConfig
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
def mcp_sandbox(tmp_path, monkeypatch):
    """Build a real config in a tmp dir and patch the MCP server module
    to use it. Returns (cfg, mcp_module)."""
    global_root = tmp_path / "home"
    (global_root / "memory").mkdir(parents=True)

    project_root = tmp_path / "repo"
    (project_root / ".claude" / "somnium" / "memory").mkdir(parents=True)
    (project_root / ".git").mkdir(parents=True)

    cfg = SomniumConfig()
    cfg.storage.global_root = str(global_root)
    cfg.embeddings.api_key = "fake-key-not-used"
    cfg.project_root = project_root

    fake = _FakeEmbedder()
    monkeypatch.setattr(memory_indexer, "get_embedder", lambda c=None: fake)

    from somnium import mcp_server

    monkeypatch.setattr(mcp_server, "get_config", lambda: cfg)

    # Force dim=4 for any ParquetStore the server creates.
    real_ps = mcp_server.ParquetStore

    def _ps(path, embedding_dim=4):
        return real_ps(path, embedding_dim=4)

    monkeypatch.setattr(mcp_server, "ParquetStore", _ps)

    # mcp_server imports get_embedder at module load (`from .embeddings
    # import get_embedder`), so we must patch the module-level symbol
    # in mcp_server, not the original location.
    monkeypatch.setattr(mcp_server, "get_embedder", lambda config=None: fake)

    return cfg, mcp_server


# ---------------------------------------------------------------------------
# memory_write
# ---------------------------------------------------------------------------


def test_memory_write_creates_global_file(mcp_sandbox):
    cfg, mcp_server = mcp_sandbox
    result_json = mcp_server.memory_write(
        content="Always use Graphite",
        scope="global",
        title="Graphite usage",
        tags=["git"],
    )
    result = json.loads(result_json)
    assert result["scope"] == "global"
    assert result["tags"] == ["git"]

    written = Path(result["written"])
    assert written.exists()
    assert written.parent == cfg.global_memory_dir
    # No date prefix — slug-only filename
    assert written.name == "graphite-usage.md"

    body = written.read_text()
    assert "Graphite usage" in body
    assert "Always use Graphite" in body
    assert "tags:" in body


def test_memory_write_creates_project_file(mcp_sandbox):
    cfg, mcp_server = mcp_sandbox
    result_json = mcp_server.memory_write(
        content="Components in src/components/shared",
        scope="project",
        title="React layout",
    )
    result = json.loads(result_json)
    written = Path(result["written"])
    assert written.parent == cfg.project_memory_dir
    assert written.name == "react-layout.md"


def test_memory_write_project_without_project_raises(tmp_path, monkeypatch):
    """When no project is detected, project-scope writes must error
    rather than silently fall back to global."""
    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "home")
    cfg.embeddings.api_key = "fake"
    cfg.project_root = None  # explicitly no project

    from somnium import mcp_server

    monkeypatch.setattr(mcp_server, "get_config", lambda: cfg)

    with pytest.raises(ValueError, match="No project detected"):
        mcp_server.memory_write(
            content="x",
            scope="project",
            title="t",
        )


def test_memory_write_overwrites_in_place(mcp_sandbox):
    """Two writes with the same title produce ONE file with the second content."""
    cfg, mcp_server = mcp_sandbox
    mcp_server.memory_write(content="version one", scope="global", title="Same key")
    mcp_server.memory_write(content="version two", scope="global", title="Same key")

    files = list(cfg.global_memory_dir.glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text()
    assert "version two" in body
    assert "version one" not in body


def test_memory_write_overwrite_preserves_created_at(mcp_sandbox):
    cfg, mcp_server = mcp_sandbox
    first = json.loads(
        mcp_server.memory_write(content="v1", scope="global", title="X")
    )
    first_path = Path(first["written"])
    first_text = first_path.read_text()
    first_created = next(
        line for line in first_text.splitlines() if line.startswith("created_at:")
    )

    mcp_server.memory_write(content="v2", scope="global", title="X")
    second_text = first_path.read_text()
    second_created = next(
        line for line in second_text.splitlines() if line.startswith("created_at:")
    )
    assert first_created == second_created
    # updated_at should be present and reflect the second write
    assert any(
        line.startswith("updated_at:") for line in second_text.splitlines()
    )


def test_memory_write_returns_json(mcp_sandbox):
    _, mcp_server = mcp_sandbox
    raw = mcp_server.memory_write(content="x", scope="global", title="y")
    parsed = json.loads(raw)
    assert "written" in parsed
    assert "scope" in parsed
    assert "tags" in parsed


# ---------------------------------------------------------------------------
# memory_search
# ---------------------------------------------------------------------------


def test_memory_search_returns_json_array(mcp_sandbox):
    _, mcp_server = mcp_sandbox
    # Seed the global index with one memory
    mcp_server.memory_write(content="hello world", scope="global", title="hi")

    raw = mcp_server.memory_search(query="hello", scope="global", top_k=5)
    hits = json.loads(raw)
    assert isinstance(hits, list)
    if hits:  # we wrote one, expect at least one match
        assert "file_path" in hits[0]
        assert "score" in hits[0]
        assert "scope" in hits[0]


def test_memory_search_filters_by_tags(mcp_sandbox):
    _, mcp_server = mcp_sandbox
    mcp_server.memory_write(content="python tips and tricks", scope="global", title="python-best-practices", tags=["python"])
    mcp_server.memory_write(content="git workflow guide", scope="global", title="git-branching-strategy", tags=["git"])
    mcp_server.memory_write(content="general notes without tags", scope="global", title="untagged-general-notes")

    # Filter by tag
    hits = json.loads(mcp_server.memory_search(query="tips", tags=["python"]))
    assert all("python" in h.get("tags", []) for h in hits)
    assert len(hits) >= 1

    # Multiple tags (OR)
    hits = json.loads(mcp_server.memory_search(query="tips", tags=["python", "git"]))
    assert len(hits) >= 2

    # Non-existent tag
    hits = json.loads(mcp_server.memory_search(query="tips", tags=["rust"]))
    assert len(hits) == 0


def test_memory_search_caps_top_k(mcp_sandbox):
    _, mcp_server = mcp_sandbox
    raw = mcp_server.memory_search(query="anything", scope="all", top_k=999)
    parsed = json.loads(raw)
    # Empty index, but the call must not crash and must respect the cap.
    assert isinstance(parsed, list)
    assert len(parsed) <= 20


# ---------------------------------------------------------------------------
# memory_status
# ---------------------------------------------------------------------------


def test_memory_status_returns_dict_with_expected_keys(mcp_sandbox):
    _, mcp_server = mcp_sandbox
    raw = mcp_server.memory_status()
    status = json.loads(raw)
    assert "global_index" in status
    assert "global_index_exists" in status
    assert "project_root" in status
    assert "voyage_key_set" in status
    assert "dream_enabled" in status


def test_memory_status_reports_voyage_key_state(mcp_sandbox):
    _, mcp_server = mcp_sandbox
    raw = mcp_server.memory_status()
    status = json.loads(raw)
    assert status["voyage_key_set"] is True


# ---------------------------------------------------------------------------
# code_search_semantic
# ---------------------------------------------------------------------------


def test_code_search_semantic_returns_empty_without_index(mcp_sandbox):
    _, mcp_server = mcp_sandbox
    raw = mcp_server.code_search_semantic(query="auth", top_k=5)
    parsed = json.loads(raw)
    assert parsed == []


# ---------------------------------------------------------------------------
# injection_debug
# ---------------------------------------------------------------------------


@pytest.fixture
def injection_state_dir(tmp_path, monkeypatch):
    """Set up a fake HOME so injection_debug reads from tmp_path."""
    fake_home = tmp_path / "fakehome"
    state_dir = fake_home / ".claude" / "somnium" / "state"
    state_dir.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    return state_dir


def test_injection_debug_reads_session_state(injection_state_dir):
    from somnium import mcp_server

    state_file = injection_state_dir / "prompt_context_sess-99.json"
    state_file.write_text(
        json.dumps(
            {
                "session_id": "sess-99",
                "n_hits": 2,
                "n_skills": 1,
                "n_memories": 1,
                "timestamp": "2026-04-12T00:00:00+00:00",
                "hits": [
                    {"title": "Git workflow", "scope": "global", "score": 0.9, "path": "~/mem/git.md"},
                    {"title": "API routes", "scope": "skill_project", "score": 0.8, "path": "~/skills/api.md"},
                ],
            }
        )
    )

    raw = mcp_server.injection_debug(session_id="sess-99")
    data = json.loads(raw)
    assert data["session_id"] == "sess-99"
    assert data["n_hits"] == 2
    assert len(data["hits"]) == 2
    assert data["hits"][0]["title"] == "Git workflow"


def test_injection_debug_finds_most_recent_without_session_id(injection_state_dir):
    import os
    import time

    from somnium import mcp_server

    old = injection_state_dir / "prompt_context_old.json"
    new = injection_state_dir / "prompt_context_new.json"
    old.write_text(json.dumps({"session_id": "old", "n_hits": 0, "hits": []}))
    new.write_text(json.dumps({"session_id": "new", "n_hits": 1, "hits": [{"title": "x"}]}))

    os.utime(old, (time.time() - 600, time.time() - 600))

    raw = mcp_server.injection_debug(session_id="")
    data = json.loads(raw)
    assert data["session_id"] == "new"


def test_injection_debug_returns_error_when_no_state(injection_state_dir):
    from somnium import mcp_server

    raw = mcp_server.injection_debug(session_id="nonexistent")
    data = json.loads(raw)
    assert "error" in data


def test_track_mcp_hits_appends_to_state(injection_state_dir):
    """memory_search results are tracked in session state."""
    from somnium import mcp_server
    from somnium.storage.vector import SearchHit

    # Seed a state file as if the hook had already written it.
    state_file = injection_state_dir / "prompt_context_mcp-test.json"
    state_file.write_text(
        json.dumps(
            {
                "session_id": "mcp-test",
                "n_hits": 1,
                "n_skills": 0,
                "n_memories": 1,
                "timestamp": "2026-04-12T00:00:00+00:00",
                "hits": [{"title": "Existing", "scope": "global", "score": 0.8, "path": "~/e.md"}],
            }
        )
    )

    # Simulate a memory_search returning a new hit.
    fake_hit = SearchHit(
        file_path=str(Path.home() / "new.md"),
        chunk_idx=0,
        scope="project",
        score=0.75,
        text="some content",
        heading_path=["New memory"],
        tags=[],
    )
    mcp_server._track_mcp_hits([fake_hit])

    data = json.loads(state_file.read_text())
    assert data["n_hits"] == 2
    assert data["n_memories"] == 2
    titles = [h["title"] for h in data["hits"]]
    assert "Existing" in titles
    assert "New memory" in titles
    new_hit = next(h for h in data["hits"] if h["title"] == "New memory")
    assert new_hit["source"] == "search"
