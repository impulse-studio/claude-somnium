"""Walk a repository collecting source files while honoring ignore rules."""

from __future__ import annotations

import os
from pathlib import Path

from .chunker import DEFAULT_CODE_EXTENSIONS

# Directories we never descend into even if not explicitly ignored.
ALWAYS_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".next",
    ".nuxt",
    "dist",
    "build",
    "target",
    ".gradle",
    ".idea",
    ".vscode",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".claude",  # don't index the hook config
}


def walk_code(
    root: Path,
    *,
    ignore: list[str] | None = None,
    extensions: set[str] | None = None,
    max_files: int | None = None,
) -> list[Path]:
    """Return a sorted list of source files under `root`.

    - Skips hidden directories and entries in ALWAYS_IGNORE_DIRS.
    - Skips any directory name in the user-provided `ignore` list.
    - Only keeps files whose suffix is in `extensions` (defaults to
      DEFAULT_CODE_EXTENSIONS).
    """
    if extensions is None:
        extensions = DEFAULT_CODE_EXTENSIONS

    ignore_set = set(ignore or []) | ALWAYS_IGNORE_DIRS
    out: list[Path] = []
    root = root.resolve()

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored dirs in place so os.walk skips them.
        pruned: list[str] = []
        for d in dirnames:
            if d.startswith(".") and d != ".github":
                continue
            if d in ignore_set:
                continue
            pruned.append(d)
        dirnames[:] = pruned

        for fn in filenames:
            if fn.startswith("."):
                # e.g. .env, .prettierrc — skip dotfiles
                continue
            p = Path(dirpath) / fn
            suffix = p.suffix.lower()
            if suffix in extensions:
                out.append(p)

        if max_files is not None and len(out) >= max_files:
            break

    out.sort()
    return out
