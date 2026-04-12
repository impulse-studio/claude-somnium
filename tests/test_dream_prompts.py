"""Tests for the dream agent prompt builders and JSON schema."""

from __future__ import annotations

import json

from somnium.dream.prompts import (
    DREAM_OUTPUT_SCHEMA,
    DREAM_SYSTEM_PROMPT,
    build_user_prompt,
    schema_json,
)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_schema_has_required_top_level_fields():
    assert DREAM_OUTPUT_SCHEMA["type"] == "object"
    assert set(DREAM_OUTPUT_SCHEMA["required"]) == {"should_persist", "summary", "items"}


def test_schema_categories_are_exactly_four():
    """global_skill must NOT be in the enum — skills are project-only."""
    enum = (
        DREAM_OUTPUT_SCHEMA["properties"]["items"]["items"]["properties"]["category"][
            "enum"
        ]
    )
    assert set(enum) == {
        "global_memory",
        "project_memory",
        "project_skill",
        "claude_md_patch",
    }
    assert "global_skill" not in enum


def test_schema_item_has_required_fields():
    item = DREAM_OUTPUT_SCHEMA["properties"]["items"]["items"]
    assert set(item["required"]) == {"category", "title", "content", "rationale"}
    # tags is optional but defined
    assert "tags" in item["properties"]


def test_schema_item_has_action_field():
    item = DREAM_OUTPUT_SCHEMA["properties"]["items"]["items"]
    assert "action" in item["properties"]
    assert item["properties"]["action"]["enum"] == ["write", "merge", "delete"]
    # action is optional — not in required
    assert "action" not in item["required"]


def test_schema_item_has_merge_sources_field():
    item = DREAM_OUTPUT_SCHEMA["properties"]["items"]["items"]
    assert "merge_sources" in item["properties"]
    assert item["properties"]["merge_sources"]["type"] == "array"
    assert "merge_sources" not in item["required"]


def test_schema_disallows_additional_properties():
    """Strict: no extra fields allowed at root or item level."""
    assert DREAM_OUTPUT_SCHEMA["additionalProperties"] is False
    item = DREAM_OUTPUT_SCHEMA["properties"]["items"]["items"]
    assert item["additionalProperties"] is False


def test_schema_json_round_trips():
    serialized = schema_json()
    parsed = json.loads(serialized)
    assert parsed == DREAM_OUTPUT_SCHEMA


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def test_system_prompt_mentions_all_four_categories():
    for category in ("global_memory", "project_memory", "project_skill", "claude_md_patch"):
        assert category in DREAM_SYSTEM_PROMPT


def test_system_prompt_does_not_mention_global_skill():
    """Defense in depth: the schema rejects global_skill, but the prompt
    must also not encourage the model to emit it."""
    # The string "global_skill" should only appear in the explanatory
    # note ("there is no global_skill"), never as a usable category.
    occurrences = DREAM_SYSTEM_PROMPT.lower().count("global_skill")
    # 0 or 1 — never a "use global_skill when..." instruction
    assert occurrences <= 1


def test_system_prompt_includes_dedup_instruction():
    assert "REUSE" in DREAM_SYSTEM_PROMPT or "reuse" in DREAM_SYSTEM_PROMPT
    assert "character-for-character" in DREAM_SYSTEM_PROMPT


def test_system_prompt_mentions_merge_delete():
    assert "merge" in DREAM_SYSTEM_PROMPT.lower()
    assert "delete" in DREAM_SYSTEM_PROMPT.lower()
    assert "merge_sources" in DREAM_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# build_user_prompt
# ---------------------------------------------------------------------------


def test_build_user_prompt_with_no_project():
    text = build_user_prompt(
        transcript_markdown="user: hi\nassistant: hello",
        project_root=None,
        global_memory_titles=[],
        project_memory_titles=[],
        existing_skills=[],
    )
    assert "(no project — general session)" in text
    assert "(none)" in text
    assert "user: hi" in text


def test_build_user_prompt_quotes_titles():
    text = build_user_prompt(
        transcript_markdown="…",
        project_root="/repo",
        global_memory_titles=["Always use Graphite"],
        project_memory_titles=["Type hints required"],
        existing_skills=["add-api-endpoint"],
    )
    assert '"Always use Graphite"' in text
    assert '"Type hints required"' in text
    assert '"add-api-endpoint"' in text


def test_build_user_prompt_truncates_long_lists():
    titles = [f"memory {i}" for i in range(50)]
    text = build_user_prompt(
        transcript_markdown="…",
        project_root="/repo",
        global_memory_titles=titles,
        project_memory_titles=[],
        existing_skills=[],
    )
    assert "and 20 more" in text  # 50 - 30 (max) = 20


def test_build_user_prompt_includes_transcript_verbatim():
    text = build_user_prompt(
        transcript_markdown="## USER\n\nbig important question",
        project_root="/repo",
        global_memory_titles=[],
        project_memory_titles=[],
        existing_skills=[],
    )
    assert "big important question" in text
    assert "## USER" in text


def test_build_user_prompt_includes_memory_counts():
    text = build_user_prompt(
        transcript_markdown="x",
        project_root="/repo",
        global_memory_titles=[f"g{i}" for i in range(5)],
        project_memory_titles=[f"p{i}" for i in range(35)],
        existing_skills=[],
    )
    assert "5 global" in text
    assert "35 project" in text
    # High project count triggers merge hint
    assert "aggressively merge" in text.lower()


def test_build_user_prompt_no_merge_hint_for_high_global_count():
    """Global memories don't trigger the merge hint — only project ones do."""
    text = build_user_prompt(
        transcript_markdown="x",
        project_root="/repo",
        global_memory_titles=[f"g{i}" for i in range(50)],
        project_memory_titles=[f"p{i}" for i in range(3)],
        existing_skills=[],
    )
    assert "50 global" in text
    assert "3 project" in text
    assert "aggressively merge" not in text.lower()


def test_build_user_prompt_no_merge_hint_when_low_count():
    text = build_user_prompt(
        transcript_markdown="x",
        project_root="/repo",
        global_memory_titles=["a", "b"],
        project_memory_titles=["c"],
        existing_skills=[],
    )
    assert "2 global" in text
    assert "aggressively merge" not in text.lower()


def test_build_user_prompt_uses_named_keyword_args():
    """Calling with positional args should fail — protects the API."""
    import pytest

    with pytest.raises(TypeError):
        build_user_prompt(
            "transcript",
            "/repo",
            [],
            [],
            [],
        )
