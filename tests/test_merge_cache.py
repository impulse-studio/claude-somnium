"""Tests for the Parquet merge driver (somnium.templates.merge_cache)."""

from __future__ import annotations

from pathlib import Path

import duckdb

from somnium.templates.merge_cache import merge


def _write_parquet(path: Path, rows: list[tuple[str, str, int, str, list[float]]]) -> None:
    """Write rows to a Parquet file via DuckDB."""
    conn = duckdb.connect(":memory:")
    conn.execute(
        "CREATE TABLE t ("
        "  file_hash VARCHAR,"
        "  model VARCHAR,"
        "  chunk_idx INTEGER,"
        "  text VARCHAR,"
        "  embedding FLOAT[]"
        ")"
    )
    for row in rows:
        conn.execute("INSERT INTO t VALUES (?, ?, ?, ?, ?)", list(row))
    conn.execute("COPY t TO ? (FORMAT PARQUET)", [str(path)])
    conn.close()


def _read_parquet(path: Path) -> list[tuple[str, str, int]]:
    """Read back (file_hash, model, chunk_idx) tuples, sorted for stable comparison."""
    conn = duckdb.connect(":memory:")
    rows = conn.execute(
        "SELECT file_hash, model, chunk_idx FROM read_parquet(?) "
        "ORDER BY file_hash, model, chunk_idx",
        [str(path)],
    ).fetchall()
    conn.close()
    return [(r[0], r[1], r[2]) for r in rows]


def test_merge_all_three_present(tmp_path: Path) -> None:
    """All three files present with overlapping rows -> deduped output."""
    ancestor = tmp_path / "ancestor.parquet"
    current = tmp_path / "current.parquet"
    other = tmp_path / "other.parquet"

    shared_row = ("hash1", "model1", 0, "shared text", [1.0, 0.0])
    _write_parquet(ancestor, [shared_row])
    _write_parquet(current, [shared_row, ("hash2", "model1", 0, "current only", [0.0, 1.0])])
    _write_parquet(other, [shared_row, ("hash3", "model1", 0, "other only", [0.5, 0.5])])

    result = merge(str(ancestor), str(current), str(other))

    assert result == 0
    rows = _read_parquet(current)
    # shared_row appears once (deduped), plus one unique row from each side
    assert len(rows) == 3
    hashes = {r[0] for r in rows}
    assert hashes == {"hash1", "hash2", "hash3"}


def test_merge_current_and_other_no_ancestor(tmp_path: Path) -> None:
    """No ancestor file -> merge current + other only."""
    ancestor = tmp_path / "ancestor.parquet"  # does not exist
    current = tmp_path / "current.parquet"
    other = tmp_path / "other.parquet"

    _write_parquet(current, [("hash1", "model1", 0, "cur", [1.0, 0.0])])
    _write_parquet(other, [("hash2", "model1", 0, "oth", [0.0, 1.0])])

    assert not ancestor.exists()
    result = merge(str(ancestor), str(current), str(other))

    assert result == 0
    rows = _read_parquet(current)
    assert len(rows) == 2
    hashes = {r[0] for r in rows}
    assert hashes == {"hash1", "hash2"}


def test_merge_only_current(tmp_path: Path) -> None:
    """Only current exists -> returns 0, current unchanged."""
    ancestor = tmp_path / "ancestor.parquet"
    current = tmp_path / "current.parquet"
    other = tmp_path / "other.parquet"

    _write_parquet(current, [("hash1", "model1", 0, "solo", [1.0, 0.0])])

    result = merge(str(ancestor), str(current), str(other))

    assert result == 0
    rows = _read_parquet(current)
    assert len(rows) == 1
    assert rows[0] == ("hash1", "model1", 0)


def test_merge_all_missing(tmp_path: Path) -> None:
    """All files missing or empty -> returns 0 immediately."""
    ancestor = tmp_path / "ancestor.parquet"
    current = tmp_path / "current.parquet"
    other = tmp_path / "other.parquet"

    result = merge(str(ancestor), str(current), str(other))

    assert result == 0
