"""Tests for dream/agent.py: prompt building, collect helpers, and cost logging."""

from __future__ import annotations

from somnium.dream.agent import (
    _collect_existing_titles,
    _collect_skill_names,
    _log_dream_cost,
    build_prompt_for,
)

# ---------------------------------------------------------------------------
# _collect_existing_titles
# ---------------------------------------------------------------------------


class TestCollectExistingTitles:
    def test_empty_dir(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        assert _collect_existing_titles(d) == []

    def test_missing_dir(self, tmp_path):
        assert _collect_existing_titles(tmp_path / "nope") == []

    def test_h1_title(self, tmp_path):
        d = tmp_path / "mem"
        d.mkdir()
        (d / "note.md").write_text("---\ntags: [test]\n---\n\n# My Title\n\nBody.\n")
        titles = _collect_existing_titles(d)
        assert titles == ["My Title"]

    def test_frontmatter_title(self, tmp_path):
        d = tmp_path / "mem"
        d.mkdir()
        (d / "note.md").write_text("---\ntitle: Front Title\n---\n\nNo H1 here.\n")
        titles = _collect_existing_titles(d)
        assert titles == ["Front Title"]

    def test_falls_back_to_stem(self, tmp_path):
        d = tmp_path / "mem"
        d.mkdir()
        (d / "some-slug.md").write_text("No frontmatter, no H1.")
        titles = _collect_existing_titles(d)
        assert titles == ["some-slug"]

    def test_limit(self, tmp_path):
        d = tmp_path / "mem"
        d.mkdir()
        for i in range(10):
            (d / f"note-{i:02d}.md").write_text(f"# Title {i}\n")
        titles = _collect_existing_titles(d, limit=3)
        assert len(titles) == 3

    def test_non_file_skipped(self, tmp_path):
        d = tmp_path / "mem"
        d.mkdir()
        (d / "subdir.md").mkdir()  # directory with .md extension
        (d / "real.md").write_text("# Real\n")
        titles = _collect_existing_titles(d)
        assert titles == ["Real"]

    def test_bad_frontmatter_falls_back(self, tmp_path):
        d = tmp_path / "mem"
        d.mkdir()
        (d / "broken.md").write_text("---\nbad: [unclosed\n---\n# H1\n")
        titles = _collect_existing_titles(d)
        assert len(titles) == 1


# ---------------------------------------------------------------------------
# _collect_skill_names
# ---------------------------------------------------------------------------


class TestCollectSkillNames:
    def test_empty_dir(self, tmp_path):
        d = tmp_path / "skills"
        d.mkdir()
        assert _collect_skill_names(d) == []

    def test_missing_dir(self, tmp_path):
        assert _collect_skill_names(tmp_path / "nope") == []

    def test_finds_skills(self, tmp_path):
        d = tmp_path / "skills"
        d.mkdir()
        (d / "skill-a").mkdir()
        (d / "skill-a" / "SKILL.md").write_text("# Skill A\n")
        (d / "skill-b").mkdir()
        (d / "skill-b" / "SKILL.md").write_text("# Skill B\n")
        (d / "not-a-skill").mkdir()  # no SKILL.md
        names = _collect_skill_names(d)
        assert set(names) == {"skill-a", "skill-b"}

    def test_limit(self, tmp_path):
        d = tmp_path / "skills"
        d.mkdir()
        for i in range(5):
            s = d / f"skill-{i}"
            s.mkdir()
            (s / "SKILL.md").write_text(f"# Skill {i}\n")
        names = _collect_skill_names(d, limit=2)
        assert len(names) == 2


# ---------------------------------------------------------------------------
# build_prompt_for
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def _make_transcript(self, **kwargs):
        from somnium.dream.transcript import Transcript, Turn

        turns = [
            Turn(role="user", text="hello"),
            Turn(role="assistant", text="hi"),
        ]
        return Transcript(
            session_id=kwargs.get("session_id", "test"),
            cwd=kwargs.get("cwd", "/tmp"),
            turns=turns,
        )

    def test_returns_string(self, tmp_path):
        from somnium.config import SomniumConfig

        cfg = SomniumConfig()
        cfg.storage.global_root = str(tmp_path / "home")
        (tmp_path / "home" / "memory").mkdir(parents=True)
        (tmp_path / "home" / "skills").mkdir(parents=True)

        t = self._make_transcript()
        result = build_prompt_for(t, cfg)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_with_project(self, tmp_path):
        from somnium.config import SomniumConfig

        cfg = SomniumConfig()
        cfg.storage.global_root = str(tmp_path / "home")
        cfg.project_root = tmp_path / "proj"
        (tmp_path / "home" / "memory").mkdir(parents=True)
        (tmp_path / "home" / "skills").mkdir(parents=True)
        (tmp_path / "proj" / ".claude" / "somnium" / "memory").mkdir(parents=True)

        t = self._make_transcript(cwd=str(tmp_path / "proj"))
        result = build_prompt_for(t, cfg)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _log_dream_cost
# ---------------------------------------------------------------------------


class TestLogDreamCost:
    def test_valid_envelope(self, tmp_path, monkeypatch):
        """Extracts cost from valid envelope."""
        import json

        logged = []
        monkeypatch.setattr(
            "somnium.cost.log_cost",
            lambda **kw: logged.append(kw),
        )
        envelope = json.dumps({
            "total_cost_usd": 0.05,
            "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 10},
        })
        _log_dream_cost(envelope, "claude-haiku-4-5", "myproject")
        assert len(logged) == 1
        assert logged[0]["cost_usd"] == 0.05
        assert logged[0]["source"] == "dream"

    def test_invalid_json_no_crash(self):
        """Invalid JSON doesn't crash."""
        _log_dream_cost("not json", "model", "proj")

    def test_no_usage_no_crash(self, monkeypatch):
        """Missing usage field doesn't crash."""
        import json

        logged = []
        monkeypatch.setattr(
            "somnium.cost.log_cost",
            lambda **kw: logged.append(kw),
        )
        envelope = json.dumps({"total_cost_usd": 0.01})
        _log_dream_cost(envelope, "model", "proj")
        assert len(logged) == 1

    def test_non_dict_envelope(self):
        """Non-dict envelope silently returns."""
        import json

        _log_dream_cost(json.dumps([1, 2, 3]), "model", "proj")
