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
    ids = {c.get("id") for c in board.get("cards") or []}
    assert "fear_greed" in ids
    assert "crypto_fear_greed" in ids
    # Extra market gauges (best-effort; at least a few beyond the core set)
    extras = ids & {"vix", "dxy", "btc", "gold", "treasury_10y", "hy_oas", "breakeven"}
    assert len(extras) >= 3
    crypto = next(c for c in board["cards"] if c["id"] == "crypto_fear_greed")
    assert crypto.get("value") is not None or crypto.get("series") == []
    assert len(board["cards"]) >= 8
