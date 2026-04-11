#!/usr/bin/env python3
"""Check that no function exceeds MAX_FUNC_LINES lines."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

MAX_FUNC_LINES = 60
EXCLUDE_NAMES = {"__init__.py", "conftest.py"}


def check(paths: list[str]) -> int:
    errors = 0
    for p in paths:
        path = Path(p)
        if path.name in EXCLUDE_NAMES or path.suffix != ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=p)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.end_lineno is None:
                    continue
                length = node.end_lineno - node.lineno + 1
                if length > MAX_FUNC_LINES:
                    print(
                        f"FAIL: {path}:{node.lineno} "
                        f"{node.name}() is {length} lines (max {MAX_FUNC_LINES})"
                    )
                    errors += 1
    return min(errors, 1)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        files = [str(p) for p in Path("somnium").rglob("*.py")]
    else:
        files = sys.argv[1:]
    sys.exit(check(files))
