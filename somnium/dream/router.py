"""Dream router: dispatch classified items to the right files on disk.

Writes each item from the dream agent's output to the correct target:

  global_memory   → ~/.claude/somnium/memory/<date>-<slug>.md
  project_memory  → <repo>/.claude/somnium/memory/<date>-<slug>.md
  global_skill    → ~/.claude/skills/<slug>/SKILL.md
  project_skill   → <repo>/.claude/skills/<slug>/SKILL.md
  claude_md_patch → append to <repo>/CLAUDE.md inside a marker block

Each successful write triggers an incremental reindex through the
indexer so the new memory is immediately searchable.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import frontmatter

if TYPE_CHECKING:
    from pathlib import Path

    from ..config import SomniumConfig

from ..indexer import index_single_file
from ..storage.parquet_store import ParquetStore

SKIPPED_CATEGORIES_WITHOUT_PROJECT = {
    "project_memory",
    "project_skill",
    "claude_md_patch",
}

CLAUDE_MD_MARKER_START = "<!-- somnium:dream:start -->"
CLAUDE_MD_MARKER_END = "<!-- somnium:dream:end -->"


@dataclass
class WriteRecord:
    category: str
    title: str
    path: str
    status: str  # written | appended | skipped | error | merged | deleted | merge_source_deleted
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "title": self.title,
            "path": self.path,
            "status": self.status,
            "reason": self.reason,
        }


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9\s-]", "", text).strip().lower()
    slug = re.sub(r"[\s-]+", "-", slug)
    return slug[:60] or "item"


def _levenshtein(a: str, b: str) -> int:
    """Minimal Levenshtein distance. No external dep."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            curr.append(
                min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (ca != cb))
            )
        prev = curr
    return prev[-1]


def _find_similar_slug(slug: str, directory: Path, max_distance: int = 3) -> str:
    """If an existing .md file has a slug within `max_distance` edits,
    return that slug instead. This collapses near-duplicate titles like
    "type-hints-required" and "type-hints-are-mandatory" onto one file.

    Returns the original slug if no near match is found.
    """
    if not directory.exists():
        return slug
    for existing in directory.glob("*.md"):
        existing_slug = existing.stem
        if existing_slug == slug:
            return slug  # exact match, no need to check further
        dist = _levenshtein(slug, existing_slug)
        if 0 < dist <= max_distance:
            return existing_slug  # use the existing slug
    return slug


def _read_existing_created_at(path: Path) -> str | None:
    """Return the existing `created_at` frontmatter value as an
    ISO-8601 string. PyYAML auto-parses dates into datetime objects on
    load, so we re-serialize via isoformat() for a stable round-trip."""
    if not path.exists():
        return None
    try:
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    value = post.metadata.get("created_at")
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def _write_memory_md(
    *,
    target_dir: Path,
    title: str,
    content: str,
    category: str,
    tags: list[str] | None,
) -> Path:
    """Write or update a memory file.

    Filename is derived from the title slug only — no date prefix —
    so subsequent dream runs that pick the same title overwrite the
    same file rather than creating duplicates. The original
    `created_at` is preserved across overwrites; an `updated_at` is
    added (or refreshed) on every write.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title)
    slug = _find_similar_slug(slug, target_dir)
    target = target_dir / f"{slug}.md"

    now = dt.datetime.now(tz=dt.UTC)
    created_at = _read_existing_created_at(target) or now.isoformat()

    fm_lines = [
        "---",
        f"created_at: {created_at}",
        f"updated_at: {now.isoformat()}",
        f"category: {category}",
        "source: dream",
    ]
    if tags:
        fm_lines.append(f"tags: {json.dumps(tags)}")
    fm_lines.append("---")

    body = "\n".join(fm_lines) + "\n\n"
    if title:
        body += f"# {title}\n\n"
    body += content.strip() + "\n"
    target.write_text(body, encoding="utf-8")
    return target


def _write_skill(*, skills_dir: Path, title: str, content: str) -> Path:
    """Write or update a skill.

    Skill directory is the title slug. If a skill with the same slug
    already exists it is overwritten — same dedup behavior as memories.
    To preserve a hand-edited skill, rename it before the dream runs.
    """
    skills_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title)
    # Check if a skill with a similar slug exists (fuzzy dedup).
    for existing in skills_dir.iterdir():
        if existing.is_dir() and _levenshtein(slug, existing.name) <= 3 and (existing / "SKILL.md").exists():  # noqa: PLR2004
            slug = existing.name
            break
    target_dir = skills_dir / slug
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "SKILL.md"

    fm = (
        "---\n"
        f"name: {title}\n"
        "description: (auto-generated by Somnium dream mode)\n"
        "source: dream\n"
        "---\n\n"
    )
    target.write_text(fm + content.strip() + "\n", encoding="utf-8")
    return target


def _append_claude_md_patch(*, project_root: Path, content: str) -> Path:
    claude_md = project_root / "CLAUDE.md"
    existing = claude_md.read_text(encoding="utf-8") if claude_md.exists() else ""

    stamp = dt.datetime.now(tz=dt.UTC).isoformat()
    block = (
        f"\n{CLAUDE_MD_MARKER_START}\n"
        f"<!-- somnium auto-appended {stamp} -->\n"
        f"{content.strip()}\n"
        f"{CLAUDE_MD_MARKER_END}\n"
    )

    # If a marker block already exists, append inside it. Otherwise
    # append to the bottom.
    if CLAUDE_MD_MARKER_START in existing and CLAUDE_MD_MARKER_END in existing:
        # Insert new content right before the END marker.
        idx = existing.rfind(CLAUDE_MD_MARKER_END)
        new_content = (
            existing[:idx]
            + f"<!-- somnium auto-appended {stamp} -->\n{content.strip()}\n"
            + existing[idx:]
        )
    else:
        new_content = existing.rstrip() + "\n" + block

    claude_md.write_text(new_content, encoding="utf-8")
    return claude_md


def _reindex_file(path: Path, kind: str, config: SomniumConfig) -> None:
    """Upsert a single file into the right vector store. Non-fatal on error."""
    if kind in {"memory_global", "skill_global"}:
        store_path = config.global_index_path
    elif kind in {"memory_project", "skill_project"}:
        if not config.project_index_path:
            return
        store_path = config.project_index_path
    else:
        return

    try:
        with ParquetStore(store_path) as store:
            index_single_file(store=store, path=path, kind=kind, config=config)
    except Exception:  # noqa: S110
        # Dream router must not blow up on a bad index; log elsewhere.
        pass


def _deindex_file(path: Path, kind: str, config: SomniumConfig) -> None:
    """Remove a file from the vector store. Non-fatal on error."""
    if kind in {"memory_global", "skill_global"}:
        store_path = config.global_index_path
    elif kind in {"memory_project", "skill_project"}:
        if not config.project_index_path:
            return
        store_path = config.project_index_path
    else:
        return

    try:
        with ParquetStore(store_path) as store:
            store.delete_file(str(path.resolve()))
    except Exception:  # noqa: S110
        pass


def _find_file_by_title(title: str, directory: Path) -> Path | None:
    """Find a memory .md file by its H1 title.

    Resolution order:
      1. Exact slug match (title → slug → file)
      2. Fuzzy slug match (Levenshtein ≤ 3)
      3. H1 content scan (read each file, compare H1)
    """
    if not directory.exists():
        return None

    slug = _slugify(title)
    exact = directory / f"{slug}.md"
    if exact.exists():
        return exact

    fuzzy_slug = _find_similar_slug(slug, directory)
    if fuzzy_slug != slug:
        fuzzy_path = directory / f"{fuzzy_slug}.md"
        if fuzzy_path.exists():
            return fuzzy_path

    for path in directory.glob("*.md"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
            h1 = re.search(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
            if h1 and h1.group(1).strip() == title:
                return path
        except Exception:  # noqa: S112
            continue

    return None


def _delete_memory_file(
    *,
    title: str,
    directory: Path,
    kind: str,
    config: SomniumConfig,
) -> WriteRecord:
    """Delete a memory file from disk and remove it from the vector index."""
    path = _find_file_by_title(title, directory)
    if path is None:
        return WriteRecord(
            category=kind,
            title=title,
            path="",
            status="skipped",
            reason=f"file not found for title: {title!r}",
        )

    abs_path = str(path.resolve())

    import contextlib

    with contextlib.suppress(Exception):
        _deindex_file(path, kind, config)

    try:
        path.unlink()
    except Exception as exc:
        return WriteRecord(
            category=kind,
            title=title,
            path=abs_path,
            status="error",
            reason=f"failed to delete: {exc}",
        )

    return WriteRecord(
        category=kind,
        title=title,
        path=abs_path,
        status="deleted",
    )


_MEMORY_CATEGORIES = {"global_memory", "project_memory"}
_MAX_PROJECT_MERGE_DELETE = 5


def _resolve_memory_dir_and_kind(
    category: str, config: SomniumConfig
) -> tuple[Path | None, str]:
    """Return (target_dir, kind) for a memory category."""
    if category == "global_memory":
        return config.global_memory_dir, "memory_global"
    if category == "project_memory":
        return (config.project_memory_dir, "memory_project")
    return None, ""


def _handle_merge(
    *,
    item: dict[str, Any],
    config: SomniumConfig,
) -> list[WriteRecord]:
    """Handle a merge action: write merged file, then delete sources."""
    records: list[WriteRecord] = []
    category = item.get("category", "")
    title = item.get("title", "").strip()
    content = item.get("content", "").strip()
    tags = item.get("tags") or []
    merge_sources = item.get("merge_sources") or []

    if not title or not content:
        records.append(WriteRecord(
            category=category, title=title, path="",
            status="skipped", reason="merge: missing title or content",
        ))
        return records

    if not merge_sources:
        records.append(WriteRecord(
            category=category, title=title, path="",
            status="skipped", reason="merge: no merge_sources provided",
        ))
        return records

    if category not in _MEMORY_CATEGORIES:
        records.append(WriteRecord(
            category=category, title=title, path="",
            status="skipped",
            reason=f"merge not supported for category: {category!r}",
        ))
        return records

    target_dir, kind = _resolve_memory_dir_and_kind(category, config)
    if target_dir is None:
        records.append(WriteRecord(
            category=category, title=title, path="",
            status="skipped", reason="no project detected",
        ))
        return records

    # Step 1: Write the merged file
    path = _write_memory_md(
        target_dir=target_dir,
        title=title,
        content=content,
        category=category,
        tags=tags,
    )
    _reindex_file(path, kind, config)
    records.append(WriteRecord(category, title, str(path), "merged"))

    # Step 2: Delete each source file (skip if same as merged file)
    for source_title in merge_sources:
        source_path = _find_file_by_title(source_title, target_dir)
        if source_path is None:
            records.append(WriteRecord(
                category=category, title=source_title, path="",
                status="skipped",
                reason=f"merge source not found: {source_title!r}",
            ))
            continue

        if source_path.resolve() == path.resolve():
            continue

        try:
            _deindex_file(source_path, kind, config)
            source_path.unlink()
            records.append(WriteRecord(
                category=category, title=source_title,
                path=str(source_path), status="merge_source_deleted",
            ))
        except Exception as exc:
            records.append(WriteRecord(
                category=category, title=source_title,
                path=str(source_path), status="error",
                reason=f"failed to delete merge source: {exc}",
            ))

    return records


def dispatch(  # noqa: PLR0912, PLR0915
    items: list[dict[str, Any]],
    config: SomniumConfig,
) -> list[WriteRecord]:
    """Write all items and return per-item records."""
    records: list[WriteRecord] = []
    project_merge_delete_count = 0

    for item in items:
        category = item.get("category", "")
        title = item.get("title", "").strip()
        content = item.get("content", "").strip()
        tags = item.get("tags") or []
        action = item.get("action", "write")
        is_project = category == "project_memory"

        # --- Handle merge action ---
        if action == "merge":
            if is_project and project_merge_delete_count >= _MAX_PROJECT_MERGE_DELETE:
                records.append(WriteRecord(
                    category=category, title=title, path="",
                    status="skipped",
                    reason=f"project merge/delete limit ({_MAX_PROJECT_MERGE_DELETE}) reached",
                ))
                continue
            if is_project:
                project_merge_delete_count += 1
            records.extend(_handle_merge(item=item, config=config))
            continue

        # --- Handle delete action ---
        if action == "delete":
            if is_project and project_merge_delete_count >= _MAX_PROJECT_MERGE_DELETE:
                records.append(WriteRecord(
                    category=category, title=title, path="",
                    status="skipped",
                    reason=f"project merge/delete limit ({_MAX_PROJECT_MERGE_DELETE}) reached",
                ))
                continue
            if is_project:
                project_merge_delete_count += 1

            if category not in _MEMORY_CATEGORIES:
                records.append(WriteRecord(
                    category=category, title=title, path="",
                    status="skipped",
                    reason=f"delete not supported for category: {category!r}",
                ))
                continue

            target_dir, kind = _resolve_memory_dir_and_kind(category, config)
            if target_dir is None:
                records.append(WriteRecord(
                    category=category, title=title, path="",
                    status="skipped", reason="no project detected",
                ))
                continue

            records.append(_delete_memory_file(
                title=title, directory=target_dir,
                kind=kind, config=config,
            ))
            continue

        # --- Handle write action (default, existing behavior) ---
        if not title or not content:
            records.append(
                WriteRecord(
                    category=category,
                    title=title,
                    path="",
                    status="skipped",
                    reason="missing title or content",
                )
            )
            continue

        # Guard: project-scoped items need a detected project root.
        if category in SKIPPED_CATEGORIES_WITHOUT_PROJECT and not config.project_root:
            records.append(
                WriteRecord(
                    category=category,
                    title=title,
                    path="",
                    status="skipped",
                    reason="no project detected",
                )
            )
            continue

        try:
            if category == "global_memory":
                path = _write_memory_md(
                    target_dir=config.global_memory_dir,
                    title=title,
                    content=content,
                    category=category,
                    tags=tags,
                )
                _reindex_file(path, "memory_global", config)
                records.append(
                    WriteRecord(category, title, str(path), "written")
                )

            elif category == "project_memory":
                assert config.project_memory_dir is not None  # noqa: S101
                path = _write_memory_md(
                    target_dir=config.project_memory_dir,
                    title=title,
                    content=content,
                    category=category,
                    tags=tags,
                )
                _reindex_file(path, "memory_project", config)
                records.append(
                    WriteRecord(category, title, str(path), "written")
                )

            elif category == "global_skill":
                records.append(
                    WriteRecord(
                        category=category,
                        title=title,
                        path="",
                        status="skipped",
                        reason="global_skill not supported — use global_memory instead",
                    )
                )

            elif category == "project_skill":
                assert config.project_root is not None  # noqa: S101
                path = _write_skill(
                    skills_dir=config.project_root / ".claude" / "skills",
                    title=title,
                    content=content,
                )
                _reindex_file(path, "skill_project", config)
                records.append(
                    WriteRecord(category, title, str(path), "written")
                )

            elif category == "claude_md_patch":
                assert config.project_root is not None  # noqa: S101
                path = _append_claude_md_patch(
                    project_root=config.project_root,
                    content=content,
                )
                records.append(
                    WriteRecord(category, title, str(path), "appended")
                )

            else:
                records.append(
                    WriteRecord(
                        category=category,
                        title=title,
                        path="",
                        status="skipped",
                        reason=f"unknown category: {category!r}",
                    )
                )

        except Exception as exc:
            records.append(
                WriteRecord(
                    category=category,
                    title=title,
                    path="",
                    status="error",
                    reason=str(exc),
                )
            )

    return records
