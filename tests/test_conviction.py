"""Conviction system: vote block integrity, transcribed cells, copy guard."""

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from kostolany.calibration import CONFIDENCE_VIEW_BY_SYMBOL, calibration_payload
from kostolany.momo import RULE_IDS, MomoFloorHead

ROOT = Path(__file__).resolve().parents[1]


def _px(n=900, seed=4):
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    ret = 0.0012 * np.sin(2 * np.pi * t / 130) + rng.normal(0, 0.01, n)
    return pd.Series(100 * np.exp(np.cumsum(ret)), index=pd.bdate_range("2019-01-02", periods=n))


def test_rule_votes_eight_columns_sum_matches_counts():
    px = _px()
    head = MomoFloorHead().fit(px)
    rv = head.rule_votes(px)
    assert list(rv.columns) == list(RULE_IDS)
    pd.testing.assert_series_equal(
        rv.sum(axis=1).rename("votes_up"), head.vote_counts(px)
    )


def test_tie_resolves_up():
    px = _px()
    head = MomoFloorHead().fit(px)
    counts = head.vote_counts(px)
    regimes, _ = head.predict(px)
    ties = counts[counts == 4].index
    if len(ties):
        assert (regimes.reindex(ties) < 3).all()


def test_vote_block_side_matches_served_regime_both_paths():
    from kostolany.engine import KostolanyEngine, fit_analyst_bundle

    # Path 1: single-model engine
    eng = KostolanyEngine(model_kind="momo")
    eng.fit_synthetic(n=900, seed=3)
    snap = eng.snapshot()
    assert snap.vote is not None
    called_up = snap.regime.startswith("A")
    assert (snap.vote["side"] == "up") == called_up
    assert len(snap.vote["rules"]) == 8
    assert snap.vote["up"] + snap.vote["down"] == 8

    # Path 2: analyst bundle
    bundle = fit_analyst_bundle("SYNTH")
    msnap = bundle["momo"].snapshot()
    assert msnap.vote is not None
    assert (msnap.vote["side"] == "up") == msnap.regime.startswith("A")
    # AI heads never carry a vote block
    for kind in ("hmm", "gbm", "tsfm"):
        assert bundle[kind].snapshot().vote is None


def test_confidence_view_cells_match_artifact_when_present():
    art = ROOT / "artifacts" / "experiments" / "confidence_menu_20260730.json"
    if not art.exists():
        pytest.skip("artifact not present (dockerignored image)")
    menu = json.loads(art.read_text(encoding="utf-8"))
    tier_map = {"unanimous": "만장일치(8-0)", "strong": "7-1", "lean": "6-2", "mixed": "분열(5-3/4-4)"}
    for sym in ("^GSPC", "BTC-USD"):
        cv = CONFIDENCE_VIEW_BY_SYMBOL[sym]
        src = menu[sym]
        for key, src_key in tier_map.items():
            assert cv["tiers"][key]["side_hit"] == pytest.approx(src["by_vote_margin"][src_key]["side"])
            assert cv["tiers"][key]["share"] == pytest.approx(src["by_vote_margin"][src_key]["share"])
        assert cv["menu"]["side_hit"] == pytest.approx(src["menu"]["side(상/하 레그)"])
        assert cv["menu"]["zone1_hit"] == pytest.approx(src["menu"]["adjacent(±1 sector arc)"])
        assert cv["menu"]["zone2_hit"] == pytest.approx(src["menu"]["within±2(5 sectors)"])
        assert cv["menu"]["exact_hit"] == pytest.approx(src["menu"]["exact6"])


def test_unmeasured_symbol_has_no_confidence_view():
    assert calibration_payload("EEM") is None
    for sym in ("^GSPC", "BTC-USD"):
        assert "confidence_view" in calibration_payload(sym)


FORBIDDEN_PATTERNS = [
    r"확신도",            # scalar-confidence vocabulary is retired from conviction copy
    r"있을 확률",          # future-probability phrasing (K7)
    r"들어올 확률",
    r"도달할 확률",
    r"권고",              # advisory vocabulary
    r"반원",
    r"동전 던지",
    r"\d+%",             # hardcoded percentages — all numbers arrive via slots
]


def _flatten_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _flatten_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _flatten_strings(v)


def test_conviction_copy_guard():
    """No forbidden sentence patterns in the conviction i18n namespace (§6)."""
    for fname in ("ko.ts", "en.ts"):
        text = (ROOT / "web" / "src" / "i18n" / fname).read_text(encoding="utf-8")
        start = text.index("conviction:")
        block = text[start:]
        # strip to the conviction object by brace matching
        depth = 0
        end = None
        for i, ch in enumerate(block):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        assert end is not None
        conv = block[: end + 1]
        for pat in FORBIDDEN_PATTERNS:
            assert not re.search(pat, conv), f"{fname}: forbidden pattern {pat!r} in conviction copy"
