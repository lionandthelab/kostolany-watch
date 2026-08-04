"""Crypto Fear & Greed connector shape (network optional via cache)."""

from __future__ import annotations

from kostolany.crypto_fear_greed import _label_en, _label_ko, get_crypto_fear_greed


def test_crypto_fng_labels():
    assert _label_en("Fear", 30) == "Fear"
    assert _label_ko("Extreme Fear", 10) == "극단적 공포"
    assert _label_ko(None, 55) == "중립"


def test_crypto_fng_payload_shape():
    out = get_crypto_fear_greed(force=False)
    assert "score" in out
    assert "label_en" in out
    assert "label_ko" in out
    assert "series" in out
    assert "disclaimer" in out or "disclaimer_en" in out
