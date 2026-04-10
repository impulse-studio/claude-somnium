"""Scope resolution helpers.

A "scope" is one of:
  - "global": memories/skills that apply across every repo
  - "project": memories scoped to a single project
  - "skill":   a Claude Code skill (global or project skills dir)

Scopes are stored as a string column in the vector store so a single
DuckDB file can serve multiple scopes (global memory + global skills
share the global index, project memory lives in its own project DB).
"""

from __future__ import annotations

from enum import StrEnum


class Scope(StrEnum):
    GLOBAL = "global"
    PROJECT = "project"
    SKILL_GLOBAL = "skill_global"
    SKILL_PROJECT = "skill_project"


def normalize_scopes(scope: str | list[str] | None) -> list[str]:
    """Turn user-facing scope strings into a list of concrete scope values.

    Accepted inputs:
      None or "all"  -> every scope
      "global"       -> [GLOBAL, SKILL_GLOBAL]
      "project"      -> [PROJECT, SKILL_PROJECT]
      "skills"       -> [SKILL_GLOBAL, SKILL_PROJECT]
      a list         -> flattened
    """
    if scope is None or scope == "all":
        return [s.value for s in Scope]
    if isinstance(scope, list):
        out: list[str] = []
        for s in scope:
            out.extend(normalize_scopes(s))
        # Dedupe preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for s in out:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        return deduped

    key = scope.lower()
    if key == "global":
        return [Scope.GLOBAL.value, Scope.SKILL_GLOBAL.value]
    if key == "project":
        return [Scope.PROJECT.value, Scope.SKILL_PROJECT.value]
    if key == "skills":
        return [Scope.SKILL_GLOBAL.value, Scope.SKILL_PROJECT.value]
    # Unknown -> pass through as-is so callers can use raw scope values.
    return [scope]
