"""E2E tests that hit the real Voyage AI API.

These verify the full indexing + search path with real embeddings:
no fake embedder, no mocked client, real 1024-dim vectors.

Cost: ~$0.01 per test run (a handful of embed calls).
"""

from __future__ import annotations

from pathlib import Path

from somnium.config import load_config
from somnium.embeddings import get_embedder
from somnium.indexer import index_directory
from somnium.storage.vector import VectorStore


def test_embed_and_search_round_trip(somnium_sandbox):
    """Write a memory file, embed it with real Voyage, search for it,
    and verify it comes back with a reasonable score."""
    somnium_home, project = somnium_sandbox
    config = load_config(project_root=project)

    # Seed a memory file
    mem_dir = somnium_home / "memory"
    (mem_dir / "graphite.md").write_text(
        "---\ntags: [git]\n---\n\n"
        "# Always use Graphite\n\n"
        "We always use `gt submit` instead of `git push`.\n",
        encoding="utf-8",
    )

    # Index with real Voyage embeddings
    with VectorStore(config.global_index_path) as store:
        stats = index_directory(
            store=store,
            directory=mem_dir,
            kind="memory_global",
            config=config,
        )
        assert stats.files_embedded == 1
        assert stats.chunks_upserted >= 1

        # Search with a real query embedding
        embedder = get_embedder(config)
        query_vec = embedder.embed_query("how should I push my branch")
        hits = store.search(query_vec, top_k=3, scopes=["global"])

    assert len(hits) >= 1
    assert hits[0].score > 0.3  # reasonable similarity
    assert "graphite" in hits[0].file_path.lower() or "graphite" in hits[0].text.lower()


def test_code_index_and_search(somnium_sandbox):
    """Index a real source file, embed it with voyage-code-3, search for it."""
    somnium_home, project = somnium_sandbox
    config = load_config(project_root=project)

    # Create a source file in the project
    src = project / "src"
    src.mkdir()
    (src / "auth.py").write_text(
        "def authenticate(token: str) -> bool:\n"
        "    return validate_jwt(token)\n",
        encoding="utf-8",
    )

    from somnium.code.indexer import index_repo_code

    assert config.project_code_index_path is not None
    with VectorStore(config.project_code_index_path) as store:
        stats = index_repo_code(root=project, store=store, config=config)
        assert stats.files_embedded >= 1

    from somnium.code.semantic import search_code

    hits = search_code("authentication function", top_k=3, config=config)
    assert len(hits) >= 1
    assert any("auth" in h.file_path.lower() for h in hits)


def test_hash_skip_on_second_index(somnium_sandbox):
    """Second index run with unchanged files should skip all."""
    somnium_home, project = somnium_sandbox
    config = load_config(project_root=project)

    mem_dir = somnium_home / "memory"
    (mem_dir / "note.md").write_text("# Note\n\nA stable note.\n")

    with VectorStore(config.global_index_path) as store:
        first = index_directory(
            store=store, directory=mem_dir, kind="memory_global", config=config
        )
        assert first.files_embedded == 1

        second = index_directory(
            store=store, directory=mem_dir, kind="memory_global", config=config
        )
        assert second.files_embedded == 0
        assert second.files_skipped == 1


def test_memory_write_mcp_tool(somnium_sandbox):
    """The memory_write MCP tool should create a file and make it
    immediately searchable via real embeddings."""
    somnium_home, project = somnium_sandbox
    config = load_config(project_root=project)

    import json

    from somnium import mcp_server

    # Patch the MCP server to use our sandbox config
    original_get_config = mcp_server.get_config
    mcp_server.get_config = lambda: config

    try:
        raw = mcp_server.memory_write(
            content="All Python functions must have type hints.",
            scope="global",
            title="Type hints required",
            tags=["python"],
        )
        result = json.loads(raw)
        written = Path(result["written"])
        assert written.exists()
        assert written.name == "type-hints-required.md"

        # Searchable immediately
        embedder = get_embedder(config)
        query_vec = embedder.embed_query("do we need type hints in python")
        with VectorStore(config.global_index_path) as store:
            hits = store.search(query_vec, top_k=3, scopes=["global"])

        assert len(hits) >= 1
        assert any("type hint" in h.text.lower() for h in hits)
    finally:
        mcp_server.get_config = original_get_config
