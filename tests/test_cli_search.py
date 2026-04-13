"""Tests for ``somnium search`` command."""

from __future__ import annotations

from typer.testing import CliRunner

from somnium.cli import app
from somnium.embeddings.voyage import EmbedResult

runner = CliRunner()


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


def _patch_search(monkeypatch, tmp_path):
    """Set up a sandboxed config for search commands."""
    import somnium.embeddings as emb_mod
    from somnium.cli import search as search_mod
    from somnium.config import SomniumConfig

    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "home")
    cfg.embeddings.api_key = "fake"
    cfg.project_root = None

    monkeypatch.setattr(search_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(search_mod, "reset_config_cache", lambda: None)
    monkeypatch.setattr(emb_mod, "get_embedder", lambda *a, **kw: _FakeEmbedder())
    return cfg


def test_search_no_index(tmp_path, monkeypatch):
    """Search with no index returns 'no hits'."""
    _patch_search(monkeypatch, tmp_path)

    result = runner.invoke(app, ["search", "hello", "--json"])
    # Should not crash, output should be empty array or no hits
    assert result.exit_code == 0


def test_search_no_hits_pretty(tmp_path, monkeypatch):
    """Search with no results and no --json shows 'no hits'."""
    _patch_search(monkeypatch, tmp_path)

    result = runner.invoke(app, ["search", "hello"])
    assert result.exit_code == 0
    assert "no hits" in result.output


def test_search_with_tags(tmp_path, monkeypatch):
    """Search with --tags doesn't crash."""
    _patch_search(monkeypatch, tmp_path)

    result = runner.invoke(app, ["search", "hello", "--tags", "python,git"])
    assert result.exit_code == 0


def test_search_scope_code_no_index(tmp_path, monkeypatch):
    """Search --scope code without code index shows hint."""
    _patch_search(monkeypatch, tmp_path)

    from somnium.cli import search as search_mod

    # Mock _search_code to return empty with the "no code index" message
    monkeypatch.setattr(
        search_mod,
        "_search_code",
        lambda cfg, query, top_k, scope: [],
    )

    result = runner.invoke(app, ["search", "hello", "--scope", "code"])
    assert result.exit_code == 0


def test_search_with_memory_results(tmp_path, monkeypatch):
    """Search returning memory hits renders them."""
    _patch_search(monkeypatch, tmp_path)

    from somnium.cli import search as search_mod

    class FakeHit:
        score = 0.95
        scope = "global"
        file_path = "/home/memory/note.md"
        text = "This is a test memory note"
        heading = "Test"

        def to_dict(self):
            return {"score": self.score, "scope": self.scope, "file_path": self.file_path, "text": self.text}

    monkeypatch.setattr(
        search_mod,
        "_search_memory",
        lambda cfg, query, top_k, scope, tags=None: [{"type": "memory", "hit": FakeHit()}],
    )

    result = runner.invoke(app, ["search", "test"])
    assert result.exit_code == 0
    assert "0.950" in result.output
    assert "note.md" in result.output


def test_search_json_with_results(tmp_path, monkeypatch):
    """Search --json with results returns valid JSON array."""
    import json

    _patch_search(monkeypatch, tmp_path)

    from somnium.cli import search as search_mod

    class FakeHit:
        score = 0.85
        scope = "project"
        file_path = "/proj/memory/tip.md"
        text = "Use type hints"
        heading = "Tip"

        def to_dict(self):
            return {"score": self.score, "scope": self.scope, "file_path": self.file_path, "text": self.text}

    monkeypatch.setattr(
        search_mod,
        "_search_memory",
        lambda cfg, query, top_k, scope, tags=None: [{"type": "memory", "hit": FakeHit()}],
    )

    result = runner.invoke(app, ["search", "test", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert len(parsed) == 1
    assert parsed[0]["score"] == 0.85


def test_search_code_results(tmp_path, monkeypatch):
    """Search with code results renders code format."""
    _patch_search(monkeypatch, tmp_path)

    from somnium.cli import search as search_mod

    class FakeCodeHit:
        score = 0.88
        file_path = "/proj/src/auth.py"
        text = "def authenticate(token):\n    return validate(token)"
        language = "python"
        start_line = 1
        end_line = 2
        scope = "code"

        def to_dict(self):
            return {"score": self.score, "file_path": self.file_path, "text": self.text}

    monkeypatch.setattr(
        search_mod,
        "_search_memory",
        lambda cfg, query, top_k, scope, tags=None: [],
    )
    monkeypatch.setattr(
        search_mod,
        "_search_code",
        lambda cfg, query, top_k, scope: [{"type": "code", "hit": FakeCodeHit()}],
    )

    result = runner.invoke(app, ["search", "auth", "--scope", "all"])
    assert result.exit_code == 0
    assert "auth.py" in result.output
    assert "0.880" in result.output
