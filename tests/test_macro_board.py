"""Smoke tests for macro board payload shape."""

from __future__ import annotations

from kostolany.macro_board import _fedwatch_proxy, get_macro_board


def test_fedwatch_proxy_buckets():
    out = _fedwatch_proxy(5.25, 4.8)
    assert out["cut"] is not None
    assert abs((out["cut"] or 0) + (out["hold"] or 0) + (out["hike"] or 0) - 100) < 0.2
    assert "note" in out


def test_macro_board_has_cards():
    board = get_macro_board(force=True)
    assert "cards" in board
    assert "disclaimer" in board
    assert "fedwatch" in board
