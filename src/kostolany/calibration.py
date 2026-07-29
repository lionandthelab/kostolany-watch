"""Measured out-of-sample calibration facts for the served regime models.

These are NOT model outputs. They are recorded measurements from a purged
walk-forward run, kept here so the UI can state how good the displayed
probabilities actually are instead of rendering a raw posterior maximum as if
it were a calibrated confidence.

Provenance: ``artifacts/experiments/phase_head_KS11_20260729T084539Z.json``
(KS11, purged walk-forward with ``anchor="end"``, 8 folds, 2,696 OOS bars
spanning 2015-07 to 2026-07, 35 independent legs, gold labels used for scoring
only). Re-run with ``scripts/run_phase_experiment.py`` and update this file
whenever the serving model changes — a stale number here is worse than none.
"""

from __future__ import annotations

from typing import Any

# Chance level for 6 classes, and the structural floor for cyclic-adjacent
# accuracy (3 of 6 classes are within one step on the ring, so 0.5 is free).
EXACT6_CHANCE = 1.0 / 6.0
ADJACENT_FLOOR = 0.5

MEASURED: dict[str, dict[str, float]] = {
    "hmm": {"exact6": 0.1977, "adjacent": 0.5723, "ece": 0.5028, "brier": 0.1961},
    "gbm": {"exact6": 0.2103, "adjacent": 0.5490, "ece": 0.4244, "brier": 0.1820},
    "tsfm": {"exact6": 0.1977, "adjacent": 0.5723, "ece": 0.5028, "brier": 0.1961},
}

# A constant predictor that always emits the class prior. It is the bar the
# served probabilities currently fail to clear on both calibration metrics.
CONSTANT_PRIOR = {"exact6": 0.1836, "adjacent": 0.5367, "ece": 0.0442, "brier": 0.1414}

NOTE_KO = (
    "표시되는 확률은 보정되지 않았습니다. 2015-07~2026-07 구간 2,696거래일 "
    "워크포워드 평가에서 6국면 정확 적중률은 약 21%(무작위 17%), 인접 국면 "
    "포함 약 57%(구조적 하한 50%)였고, 확률 보정 오차(ECE)는 0.42~0.50으로 "
    "컸습니다. 확신도는 순위 참고용이며 적중 확률이 아닙니다."
)


def calibration_payload() -> dict[str, Any]:
    """Serialisable calibration block for the watch API response."""
    return {
        "measured": MEASURED,
        "constant_prior_baseline": CONSTANT_PRIOR,
        "exact6_chance": EXACT6_CHANCE,
        "adjacent_floor": ADJACENT_FLOOR,
        "n_oos_bars": 2696,
        "n_oos_legs": 35,
        "window": "2015-07..2026-07",
        "artifact": "phase_head_KS11_20260729T084539Z.json",
        "confidence_is_calibrated": False,
        "note_ko": NOTE_KO,
    }
