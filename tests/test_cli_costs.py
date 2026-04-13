"""Tests for the ``somnium costs`` CLI helpers (somnium.cli.costs)."""

from __future__ import annotations

from somnium.cli.costs import _fmt_tokens


def test_fmt_tokens_below_1k() -> None:
    assert _fmt_tokens(500) == "500"


def test_fmt_tokens_below_1k_zero() -> None:
    assert _fmt_tokens(0) == "0"


def test_fmt_tokens_thousands() -> None:
    assert _fmt_tokens(1500) == "1.5k"


def test_fmt_tokens_exact_1k() -> None:
    assert _fmt_tokens(1000) == "1.0k"


def test_fmt_tokens_millions() -> None:
    assert _fmt_tokens(2_500_000) == "2.5M"


def test_fmt_tokens_exact_1m() -> None:
    assert _fmt_tokens(1_000_000) == "1.0M"
