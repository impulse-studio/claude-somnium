#!/usr/bin/env python3
"""Git merge driver for Somnium embedding cache Parquet files.

Merges two versions of an embedding-cache.parquet by taking the UNION
of both sides and deduplicating by (file_hash, model, chunk_idx).

Usage in .gitattributes:
    *.parquet merge=somnium-cache

Git config (registered by `somnium init`):
    [merge "somnium-cache"]
        name = Somnium embedding cache merge
        driver = python3 -m somnium.templates.merge_cache %O %A %B
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb


def merge(ancestor: str, current: str, other: str) -> int:
    """Merge ancestor/current/other Parquet files into current (in-place).

    Returns 0 on success, 1 on conflict (should not happen for
    content-addressed data).
    """
    conn = duckdb.connect(":memory:")

    # Load whichever sides exist
    parts: list[str] = []
    for label, path in [("ancestor", ancestor), ("current", current), ("other", other)]:
        if Path(path).exists() and Path(path).stat().st_size > 0:
            conn.execute(
                f"CREATE TABLE {label} AS SELECT * FROM read_parquet(?)",  # noqa: S608
                [path],
            )
            parts.append(label)

    if not parts:
        return 0

    # UNION ALL + dedup: keep the first occurrence per (file_hash, model, chunk_idx)
    union_sql = " UNION ALL ".join(f"SELECT * FROM {p}" for p in parts)  # noqa: S608
    conn.execute(f"""
        CREATE TABLE merged AS
        SELECT DISTINCT ON (file_hash, model, chunk_idx) *
        FROM ({union_sql})
        ORDER BY file_hash, model, chunk_idx
    """)  # noqa: S608

    conn.execute(
        "COPY merged TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
        [current],
    )
    conn.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:  # noqa: PLR2004
        print(f"Usage: {sys.argv[0]} %O %A %B", file=sys.stderr)
        sys.exit(1)
    sys.exit(merge(sys.argv[1], sys.argv[2], sys.argv[3]))
