"""Tests for ``somnium index`` command helpers."""

from __future__ import annotations

from typer.testing import CliRunner

from somnium.cli import index as index_mod

runner = CliRunner()


# ---------------------------------------------------------------------------
# _validate_index_args
# ---------------------------------------------------------------------------


def test_validate_project_only_no_project(tmp_path, monkeypatch):
    """project_only=True without project root exits 1."""
    import pytest
    import typer

    from somnium.config import SomniumConfig

    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "home")
    cfg.project_root = None

    with pytest.raises(typer.Exit):
        index_mod._validate_index_args(cfg, project_only=True)


def test_validate_no_api_key(tmp_path, monkeypatch):
    """Missing API key exits 1."""
    import pytest
    import typer

    from somnium.config import SomniumConfig

    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "home")
    cfg.embeddings.api_key = None
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    with pytest.raises(typer.Exit):
        index_mod._validate_index_args(cfg, project_only=False)


# ---------------------------------------------------------------------------
# _merge_stats
# ---------------------------------------------------------------------------


def test_merge_stats():
    """Stats merge adds fields correctly."""
    from somnium.indexer import IndexStats

    a = IndexStats(files_seen=1, files_embedded=1, files_skipped=0, files_deleted=0, chunks_upserted=3)
    b = IndexStats(files_seen=2, files_embedded=1, files_skipped=1, files_deleted=0, chunks_upserted=5)
    index_mod._merge_stats(a, b)
    assert a.files_seen == 3
    assert a.files_embedded == 2
    assert a.files_skipped == 1
    assert a.chunks_upserted == 8


# ---------------------------------------------------------------------------
# _print_index_stats
# ---------------------------------------------------------------------------


def test_print_index_stats():
    """Smoke test — just check it doesn't crash."""
    from somnium.indexer import IndexStats

    stats = IndexStats(files_seen=5, files_embedded=3, files_skipped=2, files_deleted=0, chunks_upserted=10)
    index_mod._print_index_stats(stats, scope="global")


# ---------------------------------------------------------------------------
# _index_code errors
# ---------------------------------------------------------------------------


def test_index_code_no_project(tmp_path, monkeypatch):
    """--code without project root exits 1."""
    import pytest
    import typer

    from somnium.config import SomniumConfig

    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "home")
    cfg.project_root = None

    with pytest.raises(typer.Exit):
        index_mod._index_code(cfg)


# ---------------------------------------------------------------------------
# CLI command (minimal, mocked)
# ---------------------------------------------------------------------------


def test_index_no_api_key(tmp_path, monkeypatch):
    """index fails gracefully with no API key."""
    from somnium.config import SomniumConfig

    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "home")
    cfg.embeddings.api_key = None
    cfg.project_root = None
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    monkeypatch.setattr(index_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(index_mod, "reset_config_cache", lambda: None)

    from somnium.cli import app

    result = runner.invoke(app, ["index"])
    assert result.exit_code == 1
    assert "Voyage API key" in result.output


def test_reindex_is_alias(tmp_path, monkeypatch):
    """reindex calls index."""
    from somnium.config import SomniumConfig

    cfg = SomniumConfig()
    cfg.storage.global_root = str(tmp_path / "home")
    cfg.embeddings.api_key = None
    cfg.project_root = None
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)

    monkeypatch.setattr(index_mod, "get_config", lambda: cfg)
    monkeypatch.setattr(index_mod, "reset_config_cache", lambda: None)

    from somnium.cli import app

    result = runner.invoke(app, ["reindex"])
    # Same error as index — proves it delegates
    assert result.exit_code == 1
