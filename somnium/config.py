"""Configuration loading and merging for Somnium.

Two-layer config:
  1. Global: ~/.claude/somnium/config.toml
  2. Project (optional): <repo>/.claude/somnium/project.toml

Project keys override global keys. Missing files fall back to packaged
defaults in somnium/templates/config.toml.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class EmbeddingsConfig(BaseModel):
    provider: str = "voyage"
    model_text: str = "voyage-3.5"
    model_code: str = "voyage-code-3"
    api_key_env: str = "VOYAGE_API_KEY"
    api_key: str | None = None
    batch_size: int = 64

    def resolve_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            return os.environ.get(self.api_key_env)
        return None


class StorageConfig(BaseModel):
    global_root: str = "~/.claude/somnium"
    project_marker: str = ".claude/somnium"

    @property
    def global_root_path(self) -> Path:
        return Path(self.global_root).expanduser().resolve()


class DreamGateConfig(BaseModel):
    min_user_messages: int = 3
    skip_patterns: list[str] = Field(default_factory=list)


class DreamConfig(BaseModel):
    enabled: bool = True
    model: str = "claude-sonnet-4-6"
    gate_model: str = "claude-haiku-4-5"
    gate: DreamGateConfig = Field(default_factory=DreamGateConfig)


class ContextInjectionConfig(BaseModel):
    enabled: bool = True
    top_k: int = 5
    context_budget_tokens: int = 2000
    scopes: list[str] = Field(default_factory=lambda: ["project", "global", "skills"])


class CodeSearchConfig(BaseModel):
    symbolic_enabled: bool = True
    semantic_enabled: bool = True
    semantic_chunk_lines: int = 40
    ignore: list[str] = Field(
        default_factory=lambda: ["node_modules", ".venv", "venv", "dist", "build", ".git"]
    )


class SomniumConfig(BaseModel):
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    dream: DreamConfig = Field(default_factory=DreamConfig)
    context_injection: ContextInjectionConfig = Field(default_factory=ContextInjectionConfig)
    code_search: CodeSearchConfig = Field(default_factory=CodeSearchConfig)

    # Runtime-populated paths
    project_root: Path | None = None

    @property
    def global_root(self) -> Path:
        return self.storage.global_root_path

    @property
    def global_memory_dir(self) -> Path:
        return self.global_root / "memory"

    @property
    def global_skills_dir(self) -> Path:
        return self.global_root / "skills"

    @property
    def global_index_path(self) -> Path:
        return self.global_root / "index.parquet"

    @property
    def dream_dir(self) -> Path:
        return self.global_root / "dream"

    @property
    def project_dir(self) -> Path | None:
        if not self.project_root:
            return None
        return self.project_root / self.storage.project_marker

    @property
    def project_memory_dir(self) -> Path | None:
        if not self.project_dir:
            return None
        return self.project_dir / "memory"

    @property
    def project_index_path(self) -> Path | None:
        if not self.project_dir:
            return None
        return self.project_dir / "index.parquet"

    @property
    def project_code_index_path(self) -> Path | None:
        if not self.project_dir:
            return None
        return self.project_dir / "code-index.parquet"



def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _default_config_dict() -> dict[str, Any]:
    """Load packaged default config.toml template."""
    with resources.files("somnium.templates").joinpath("config.toml").open("rb") as fh:
        return tomllib.load(fh)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base (non-mutating)."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def find_project_root(start: Path | None = None) -> Path | None:
    """Walk upwards from `start` looking for a `.claude/somnium/` marker
    or a `.git` directory. Returns None if neither is found."""
    cwd = (start or Path.cwd()).resolve()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / ".claude" / "somnium").is_dir():
            return candidate
        if (candidate / ".git").is_dir():
            return candidate
    return None


def load_config(project_root: Path | None = None) -> SomniumConfig:
    """Load the effective config: defaults <- global <- project.

    The global root is resolved from (in priority order):
      1. SOMNIUM_HOME environment variable (useful for tests/sandboxes)
      2. [storage].global_root in the packaged default config
    """
    merged = _default_config_dict()

    # Global config
    env_home = os.environ.get("SOMNIUM_HOME")
    if env_home:
        global_root = Path(env_home).expanduser().resolve()
        # Mirror into the defaults so property accessors see it
        merged.setdefault("storage", {})["global_root"] = str(global_root)
    else:
        defaults_storage = merged.get("storage", {})
        global_root_str = defaults_storage.get("global_root", "~/.claude/somnium")
        global_root = Path(global_root_str).expanduser()

    global_config_path = global_root / "config.toml"
    merged = _deep_merge(merged, _load_toml(global_config_path))

    # Re-apply SOMNIUM_HOME after the config-file merge — env var must
    # always win over whatever [storage].global_root the config.toml says.
    if env_home:
        merged.setdefault("storage", {})["global_root"] = str(global_root)

    # Project config
    if project_root is None:
        project_root = find_project_root()
    if project_root is not None:
        project_marker = merged.get("storage", {}).get("project_marker", ".claude/somnium")
        project_config_path = project_root / project_marker / "project.toml"
        merged = _deep_merge(merged, _load_toml(project_config_path))

    config = SomniumConfig.model_validate(merged)
    config.project_root = project_root
    return config


@lru_cache(maxsize=1)
def get_config() -> SomniumConfig:
    """Cached accessor for the effective config in the current process."""
    return load_config()


def reset_config_cache() -> None:
    """Clear the cached config (useful in tests)."""
    get_config.cache_clear()
