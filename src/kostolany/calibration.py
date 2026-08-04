"""Measured out-of-sample calibration facts for the served regime models.

These are NOT model outputs. They are recorded measurements from purged
walk-forward runs of the EXACT serving configurations — post-S0 stack (FRED
publication lags, feature dedup, label B3 fix) WITH the lambda-uniform anchor
that `fit_analyst_bundle` now applies to every served arm. argmax is
anchor-invariant, so regime calls match the unanchored stack; only the
probability mass differs.

Provenance (both runs: purged walk-forward ``anchor="end"``, 8 folds,
min_train=1260, ``--enrich-fred``, gold labels scoring only; lambda fitted
causally per fold on the trailing 252 train bars):

  ^GSPC   ``artifacts/experiments/phase_head_GSPC_20260730T030031Z.json``
          2,816 OOS bars, 37 legs
  BTC-USD ``artifacts/experiments/phase_head_BTC-USD_20260730T030119Z.json``
          2,984 OOS bars, 42 legs

Gate G14 (served ECE <= 0.15 on all arms) FAILED — only hmm on BTC clears it —
so per kill condition K7 ``confidence_is_calibrated`` stays false permanently
and the UI must keep stating measured accuracy instead of claiming calibration.

The zero-parameter momentum floor (side_panel_20260730T025704Z.json, kill K2)
outperforms every served arm on side accuracy; its panel numbers ride along in
the payload so no surface can claim the fitted arms are the accuracy ceiling.
"""

from __future__ import annotations

import math
from typing import Any

from kostolany.momo import MEASURED_THIRD_GIVEN_SIDE

# Chance level for 6 classes, and the structural floor for cyclic-adjacent
# accuracy (3 of 6 classes are within one step on the ring, so 0.5 is free).
EXACT6_CHANCE = 1.0 / 6.0
ADJACENT_FLOOR = 0.5
# Brier of the uniform predictor p=1/6 under this repo's class-mean convention:
# exactly 5/36, invariant to the label distribution. This is the calibration
# floor to clear — prior_shrunk loses to it on the stored runs.
UNIFORM_BRIER = 5.0 / 36.0

MEASURED_BY_SYMBOL: dict[str, dict[str, Any]] = {
    "^GSPC": {
        "measured": {
            # momo = the K2 winner (majority-of-8 trend vote + causal clock,
            # zero fitted parameters, measurement-matched posterior). Scored on
            # the same OOS window with clock cuts fitted strictly pre-OOS.
            # Its Brier BEATS the uniform floor — the only component that does.
            "momo": {"exact6": 0.2589, "adjacent": 0.6431, "ece": 0.0092, "brier": 0.1338},
            "hmm": {"exact6": 0.2301, "adjacent": 0.6268, "ece": 0.1697, "brier": 0.1443},
            "gbm": {"exact6": 0.2053, "adjacent": 0.5866, "ece": 0.4793, "brier": 0.1936},
            "tsfm": {"exact6": 0.2074, "adjacent": 0.6168, "ece": 0.2949, "brier": 0.1595},
        },
        "constant_prior_baseline": {
            "exact6": 0.1964,
            "adjacent": 0.5827,
            "ece": 0.0360,
            "brier": 0.1431,
        },
        "uniform_baseline": {
            "exact6": 0.1964,
            "adjacent": 0.5827,
            "ece": 0.0521,
            "brier": 0.1389,
        },
        # Zero-fitted-parameter momentum family: fold-median of the 8 rules
        # plus the shipping majority-vote head's own side accuracy.
        "momo_floor": {"side_median": 0.7006, "exact6_median": 0.2615, "vote_side": 0.7170},
        "n_oos_bars": 2816,
        "n_oos_legs": 37,
        "window": "2015-05..2026-07",
        "artifact": "phase_head_GSPC_20260730T030031Z.json",
    },
    "BTC-USD": {
        "measured": {
            "momo": {"exact6": 0.2450, "adjacent": 0.6361, "ece": 0.0048, "brier": 0.1343},
            "hmm": {"exact6": 0.2369, "adjacent": 0.6347, "ece": 0.1078, "brier": 0.1413},
            "gbm": {"exact6": 0.2554, "adjacent": 0.6475, "ece": 0.3859, "brier": 0.1711},
            "tsfm": {"exact6": 0.2584, "adjacent": 0.6625, "ece": 0.2198, "brier": 0.1488},
        },
        "constant_prior_baseline": {
            "exact6": 0.1853,
            "adjacent": 0.5137,
            "ece": 0.1956,
            "brier": 0.1499,
        },
        "uniform_baseline": {
            "exact6": 0.1853,
            "adjacent": 0.5137,
            "ece": 0.0187,
            "brier": 0.1389,
        },
        "momo_floor": {"side_median": 0.6731, "exact6_median": 0.2413, "vote_side": 0.7071},
        "n_oos_bars": 2984,
        "n_oos_legs": 42,
        "window": "2018-05..2026-07",
        "artifact": "phase_head_BTC-USD_20260730T030119Z.json",
    },
}


# Transcribed cell-for-cell from artifacts/experiments/confidence_menu_20260730.json
# (the artifacts/ tree is dockerignored, so runtime loads are not an option).
# Binning 8-0 / 7-1 / 6-2 / split(5-3,4-4) is FROZEN with the artifact —
# re-binning after observing the display is fitting-by-selection: forbidden.
# All displayed percentages derive from these cells via floor() only.
CONFIDENCE_VIEW_BY_SYMBOL: dict[str, dict[str, Any]] = {
    "^GSPC": {
        "source": "confidence_menu_20260730.json",
        "measured_at": "2026-07-30",
        "n_bars": 2816,
        "n_legs": 37,
        "rounding": "floor",
        "menu": {
            "side_hit": 0.717,
            "zone1_hit": 0.6431,
            "zone2_hit": 0.8771,
            "exact_hit": 0.2589,
            "third_given_side": 0.3611,
            "exact_ceiling": MEASURED_THIRD_GIVEN_SIDE,  # structural, not measured
        },
        "tiers": {
            "unanimous": {"split": "8-0", "side_hit": 0.8062, "zone1_hit": 0.6868, "exact_hit": 0.2638, "share": 0.5533},
            "strong": {"split": "7-1", "side_hit": 0.6748, "zone1_hit": 0.6459, "exact_hit": 0.2606, "share": 0.1594},
            "lean": {"split": "6-2", "side_hit": 0.6036, "zone1_hit": 0.5782, "exact_hit": 0.2655, "share": 0.0977},
            "mixed": {"split": "5-3/4-4", "side_hit": 0.5506, "zone1_hit": 0.5468, "exact_hit": 0.2397, "share": 0.1896},
        },
    },
    "BTC-USD": {
        "source": "confidence_menu_20260730.json",
        "measured_at": "2026-07-30",
        "n_bars": 2984,
        "n_legs": 42,
        "rounding": "floor",
        "menu": {
            "side_hit": 0.7071,
            "zone1_hit": 0.6361,
            "zone2_hit": 0.9021,
            "exact_hit": 0.245,
            "third_given_side": 0.3464,
            "exact_ceiling": MEASURED_THIRD_GIVEN_SIDE,
        },
        "tiers": {
            "unanimous": {"split": "8-0", "side_hit": 0.7981, "zone1_hit": 0.6677, "exact_hit": 0.2035, "share": 0.4266},
            "strong": {"split": "7-1", "side_hit": 0.6767, "zone1_hit": 0.6399, "exact_hit": 0.2881, "share": 0.2001},
            "lean": {"split": "6-2", "side_hit": 0.6499, "zone1_hit": 0.6139, "exact_hit": 0.2638, "share": 0.1397},
            "mixed": {"split": "5-3/4-4", "side_hit": 0.6011, "zone1_hit": 0.5882, "exact_hit": 0.2726, "share": 0.2336},
        },
    },
}


def pct_floor(x: float) -> int:
    """Display percentages always floor (spec §0.3). Never round.

    §6 #9 names rounding up as a forbidden rendering (81% from 0.8062). This is
    the server-side twin of the frontend `pctFloor` in eggGeometry.ts — both
    must agree or the same quantity appears twice with different values.
    """
    return math.floor(x * 100)


def _note_ko(block: dict[str, Any]) -> str:
    m = block["measured"]
    exact_lo = pct_floor(min(v["exact6"] for v in m.values()))
    exact_hi = pct_floor(max(v["exact6"] for v in m.values()))
    exact_txt = f"{exact_lo}%" if exact_lo == exact_hi else f"{exact_lo}~{exact_hi}%"
    ece_lo = min(v["ece"] for v in m.values())
    ece_hi = max(v["ece"] for v in m.values())
    momo = block.get("momo_floor", {})
    vote_side = momo.get("vote_side", momo.get("side_median", 0))
    return (
        f"{block['window']} 구간 {block['n_oos_bars']:,}봉 워크포워드 실측: "
        f"6국면 정확 적중률 약 {exact_txt}(무작위로 찍으면 6분의 1), "
        f"보정 오차(ECE) {ece_lo:.2f}~{ece_hi:.2f}. 기본 헤드(추세 규칙)는 "
        "학습된 모델이 아니며, 표시 확률이 실측 적중률과 일치하도록 고정되어 "
        f"있습니다(상승/하락 레그 적중 {pct_floor(vote_side)}%). "
        "AI 3종의 확신도는 순위 참고용이며 적중 확률이 아닙니다."
    )


def calibration_payload(symbol: str) -> dict[str, Any] | None:
    """Serialisable calibration block for one watch symbol.

    Returns ``None`` for symbols with no measured run — the UI must show
    nothing rather than a number measured on a different market.
    """
    if not symbol:
        return None
    block = MEASURED_BY_SYMBOL.get(symbol) or MEASURED_BY_SYMBOL.get(symbol.upper())
    if block is None:
        return None
    confidence_view = CONFIDENCE_VIEW_BY_SYMBOL.get(symbol) or CONFIDENCE_VIEW_BY_SYMBOL.get(
        symbol.upper()
    )
    payload: dict[str, Any] = {
        "measured": block["measured"],
        "constant_prior_baseline": block["constant_prior_baseline"],
        "uniform_baseline": block["uniform_baseline"],
        "uniform_brier_floor": UNIFORM_BRIER,
        "momo_floor": block["momo_floor"],
        "exact6_chance": EXACT6_CHANCE,
        "adjacent_floor": ADJACENT_FLOOR,
        "n_oos_bars": block["n_oos_bars"],
        "n_oos_legs": block["n_oos_legs"],
        "window": block["window"],
        "artifact": block["artifact"],
        # Gate G14 failed (K7): this flag is permanently false until a future
        # run clears ECE <= 0.15 on every served arm.
        "confidence_is_calibrated": False,
        "note_ko": _note_ko(block),
    }
    # Unmeasured symbol -> no key at all: the UI must never render a borrowed
    # table (same contract as the calibration block itself).
    if confidence_view is not None:
        payload["confidence_view"] = confidence_view
    return payload
