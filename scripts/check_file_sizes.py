#!/usr/bin/env python3
"""Check that no Python source file exceeds MAX_LINES lines."""

from __future__ import annotations

import sys
from pathlib import Path

MAX_LINES = 500
EXCLUDE_NAMES = {"__init__.py", "conftest.py"}


def check(paths: list[str]) -> int:
    errors = 0
    for p in paths:
        path = Path(p)
        if path.name in EXCLUDE_NAMES or path.suffix != ".py":
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > MAX_LINES:
            print(f"FAIL: {path} has {lines} lines (max {MAX_LINES})")
            errors += 1
    return min(errors, 1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Default: scan somnium/
        files = [str(p) for p in Path("somnium").rglob("*.py")]
    else:
        files = sys.argv[1:]
    sys.exit(check(files))
