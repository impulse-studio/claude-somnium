"""Shared helpers for Claude Code hook entry points.

Hooks are short-lived subprocesses invoked by Claude Code. They:
  - read a JSON event object from stdin
  - do their work quickly (ideally <200ms)
  - exit 0 on success
  - write optional structured feedback to stdout
  - write error messages to stderr

These helpers centralize the boilerplate so each hook module stays thin.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import load_config

if TYPE_CHECKING:
    from ..config import SomniumConfig
from ..storage.scope import Scope


def read_event() -> dict[str, Any]:
    """Read the hook event JSON from stdin. Returns empty dict if stdin
    is empty or invalid — hooks should degrade gracefully."""
    try:
        raw = sys.stdin.read()
        if not raw:
            return {}
        return json.loads(raw)
    except Exception:
        return {}


def log_error(hook_name: str, exc: BaseException) -> None:
    """Log an exception to the Somnium hook log without crashing Claude
    Code. Hooks should never propagate exceptions back."""
    try:
        cfg = load_config()
        log_dir = cfg.global_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "hooks.log"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(
                f"[{dt.datetime.now(tz=dt.UTC).isoformat()}] {hook_name}: {exc!r}\n"
                f"{traceback.format_exc()}\n"
            )
    except Exception:  # noqa: S110 — last-resort swallow
        pass
    # Also write a one-liner to stderr for visible feedback
    sys.stderr.write(f"[somnium:{hook_name}] {exc!r}\n")


def log_info(hook_name: str, message: str) -> None:
    """Append an info line to the Somnium hook log."""
    try:
        cfg = load_config()
        log_dir = cfg.global_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "hooks.log"
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{dt.datetime.now(tz=dt.UTC).isoformat()}] {hook_name}: {message}\n")
    except Exception:  # noqa: S110
        pass


# ---------------------------------------------------------------------------
# Path routing
# ---------------------------------------------------------------------------


class PathRoute:
    """Classification of a file path into a Somnium scope."""

    __slots__ = ("kind", "root", "scope", "store_path")

    def __init__(self, scope: str, store_path: Path, kind: str, root: Path) -> None:
        self.scope = scope
        self.store_path = store_path
        self.kind = kind
        self.root = root

    def __repr__(self) -> str:  # pragma: no cover - debug
        return f"PathRoute(scope={self.scope}, kind={self.kind}, root={self.root})"


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except (ValueError, FileNotFoundError):
        # FileNotFoundError can happen if `path` doesn't exist yet;
        # fall back to string comparison.
        return str(path.resolve(strict=False)).startswith(
            str(parent.resolve(strict=False))
        )
    else:
        return True


def classify_path(path: Path, config: SomniumConfig) -> PathRoute | None:
    """Return a PathRoute if `path` belongs to a Somnium-managed dir.

    Priority:
      1. Global memory directory
      2. Global skills directory
      3. Project memory directory
      4. Project skills directory (<repo>/.claude/skills)

    Files outside every managed directory return None.
    """
    path = Path(path)

    # Global memory
    if _is_under(path, config.global_memory_dir):
        return PathRoute(
            scope=Scope.GLOBAL.value,
            store_path=config.global_index_path,
            kind="memory_global",
            root=config.global_memory_dir,
        )

    # Global skills
    if _is_under(path, config.global_skills_dir):
        return PathRoute(
            scope=Scope.SKILL_GLOBAL.value,
            store_path=config.global_index_path,
            kind="skill_global",
            root=config.global_skills_dir,
        )

    # Project memory / skills (only if a project is detected)
    if config.project_root is not None:
        if config.project_memory_dir and _is_under(path, config.project_memory_dir):
            assert config.project_index_path is not None  # noqa: S101
            return PathRoute(
                scope=Scope.PROJECT.value,
                store_path=config.project_index_path,
                kind="memory_project",
                root=config.project_memory_dir,
            )
        project_skills = config.project_root / ".claude" / "skills"
        if _is_under(path, project_skills):
            assert config.project_index_path is not None  # noqa: S101
            return PathRoute(
                scope=Scope.SKILL_PROJECT.value,
                store_path=config.project_index_path,
                kind="skill_project",
                root=project_skills,
            )

    return None
