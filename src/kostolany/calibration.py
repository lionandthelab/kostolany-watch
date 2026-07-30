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

from typing import Any

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
        # Zero-fitted-parameter momentum family (8 rules, median), same folds.
        "momo_floor": {"side_median": 0.7006, "exact6_median": 0.2615},
        "n_oos_bars": 2816,
        "n_oos_legs": 37,
        "window": "2015-05..2026-07",
        "artifact": "phase_head_GSPC_20260730T030031Z.json",
    },
    "BTC-USD": {
        "measured": {
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
        "momo_floor": {"side_median": 0.6731, "exact6_median": 0.2413},
        "n_oos_bars": 2984,
        "n_oos_legs": 42,
        "window": "2018-05..2026-07",
        "artifact": "phase_head_BTC-USD_20260730T030119Z.json",
    },
}


def _note_ko(block: dict[str, Any]) -> str:
    m = block["measured"]
    exact_lo = round(min(v["exact6"] for v in m.values()) * 100)
    exact_hi = round(max(v["exact6"] for v in m.values()) * 100)
    exact_txt = f"{exact_lo}%" if exact_lo == exact_hi else f"{exact_lo}~{exact_hi}%"
    ece_lo = min(v["ece"] for v in m.values())
    ece_hi = max(v["ece"] for v in m.values())
    momo = block.get("momo_floor", {})
    return (
        "표시되는 확률은 보정 보증이 없습니다(uniform 앵커 적용). "
        f"{block['window']} 구간 {block['n_oos_bars']:,}봉 워크포워드 평가에서 "
        f"6국면 정확 적중률은 약 {exact_txt}"
        f"(무작위 {EXACT6_CHANCE * 100:.0f}%)였고, 보정 오차(ECE)는 "
        f"{ece_lo:.2f}~{ece_hi:.2f}였습니다. 참고로 학습 없는 추세 규칙의 "
        f"상승/하락 적중률은 {momo.get('side_median', 0) * 100:.0f}%로 "
        "세 모델보다 높았습니다. 확신도는 순위 참고용이며 적중 확률이 아닙니다."
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
    return {
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
