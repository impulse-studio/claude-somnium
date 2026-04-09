"""Tests for scope normalization."""

from somnium.storage.scope import Scope, normalize_scopes


def test_normalize_all():
    result = normalize_scopes("all")
    assert Scope.GLOBAL.value in result
    assert Scope.PROJECT.value in result
    assert Scope.SKILL_GLOBAL.value in result
    assert Scope.SKILL_PROJECT.value in result


def test_normalize_none():
    assert normalize_scopes(None) == normalize_scopes("all")


def test_normalize_global_expands_to_skill_global():
    result = normalize_scopes("global")
    assert Scope.GLOBAL.value in result
    assert Scope.SKILL_GLOBAL.value in result
    assert Scope.PROJECT.value not in result


def test_normalize_project_expands_to_skill_project():
    result = normalize_scopes("project")
    assert Scope.PROJECT.value in result
    assert Scope.SKILL_PROJECT.value in result
    assert Scope.GLOBAL.value not in result


def test_normalize_skills():
    result = normalize_scopes("skills")
    assert Scope.SKILL_GLOBAL.value in result
    assert Scope.SKILL_PROJECT.value in result
    assert Scope.GLOBAL.value not in result


def test_normalize_list_dedupes():
    result = normalize_scopes(["global", "skills"])
    # global and skills both include skill_global — should appear once
    assert result.count(Scope.SKILL_GLOBAL.value) == 1
