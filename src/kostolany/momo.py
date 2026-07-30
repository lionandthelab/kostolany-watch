"""MomoFloorHead — the zero-fitted-parameter regime head that won.

Kill condition K2 of the pre-registered SideHead program fired on
2026-07-30 (artifacts/experiments/side_panel_20260730T025704Z.json):
the fitted logistic head lost to the unfitted momentum family median by
-4.9pp panel-wide (0.6245 vs 0.6713 gold-side accuracy, 12 instruments,
paired two-level bootstrap). Per the pre-registration, the floor ships and
the fitted head was deleted. THE REGIME HEAD CONTAINS NO FITTED MODEL —
that sentence is a product claim and must stay true while this class serves.

Construction (all causal, zero fitted parameters):
  side   majority vote of the pre-registered 8-rule family:
         close > MA_w for w in {20,40,60,100,200}; ret_h > 0 for h in {10,20,60}
  third  point-in-time turn clock (labels_pit), terciles fitted on the
         observed history of ``k`` — a train statistic, not a parameter
  proba  uniform-anchored: per K3 the posterior ships as the uniform floor
         blended with the one-hot call at the MEASURED panel side accuracy,
         so the displayed mass never claims more than what was measured.

Measured panel medians (same artifact): side 0.6713, exact6 ~ 0.244.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from kostolany.labels_pit import clock_terciles, clock_third, pit_state

N_CLASSES = 6
_COLS = [f"p{i}" for i in range(N_CLASSES)]

MA_WINDOWS = (20, 40, 60, 100, 200)
RET_HORIZONS = (10, 20, 60)
# Public rule identifiers, column order frozen — the UI's rule ledger renders
# these by name so anyone can verify each vote in any charting app.
RULE_IDS = ("ma20", "ma40", "ma60", "ma100", "ma200", "ret10", "ret20", "ret60")

# Panel-measured side accuracy of the family median (12 instruments,
# side_panel_20260730T025704Z.json). Used ONLY to bound the displayed
# probability mass; re-measure and update alongside calibration.py.
MEASURED_PANEL_SIDE_ACCURACY = 0.6713
# Measured third|side of the causal clock (G10 band, re-anchor artifacts:
# 0.373 median). The called third's share of the side mass — NOT learnable
# beyond this (gold's third is future-defined), so never raise it.
MEASURED_THIRD_GIVEN_SIDE = 0.372


class MomoFloorHead:
    """fit/predict surface compatible with the other regime heads.

    ``fit`` records clock tercile cuts from the training slice (a statistic of
    elapsed-bar counts, not an optimised parameter) and nothing else.
    """

    def __init__(self, *, min_cycle: int = 60) -> None:
        self.min_cycle = int(min_cycle)
        self.cuts_: tuple[float, float] = (10.0, 25.0)

    # The head consumes PRICES, not the feature matrix — by construction it
    # cannot overfit features because it never sees them.
    def fit(self, prices: pd.Series) -> "MomoFloorHead":
        clock = pit_state(prices.astype(float), min_cycle=self.min_cycle)
        self.cuts_ = clock_terciles(clock.loc[clock["side"] != 0, "k"])
        return self

    def rule_votes(self, prices: pd.Series) -> pd.DataFrame:
        """Per-rule UP votes (bool), one column per RULE_IDS. Causal, zero params.

        Column sum equals ``vote_counts``. The split is the head's native
        conviction signal: measured side accuracy is 0.80/0.79 on unanimous
        bars vs 0.55/0.60 on split bars (confidence_menu_20260730.json) —
        a measured conditional TABLE, never a fit.
        """
        px = prices.astype(float)
        cols: dict[str, pd.Series] = {}
        for w in MA_WINDOWS:
            cols[f"ma{w}"] = px > px.rolling(w, min_periods=w // 2).mean()
        for h in RET_HORIZONS:
            cols[f"ret{h}"] = px.pct_change(h) > 0
        return pd.DataFrame(cols, index=px.index)[list(RULE_IDS)]

    def vote_counts(self, prices: pd.Series) -> pd.Series:
        """Per-bar count of the 8 rules voting UP (0..8)."""
        return self.rule_votes(prices).sum(axis=1).rename("votes_up")

    def _side_up(self, prices: pd.Series) -> pd.Series:
        # Tie (4-4) resolves UP by pre-registered rule; disclosed in the UI.
        return self.vote_counts(prices) >= (len(MA_WINDOWS) + len(RET_HORIZONS)) / 2.0

    def predict(self, prices: pd.Series) -> tuple[pd.Series, pd.DataFrame]:
        px = prices.astype(float)
        up = self._side_up(px)
        clock = pit_state(px, min_cycle=self.min_cycle)
        thirds = clock_third(clock["k"], self.cuts_)
        classes = np.where(up.to_numpy(), thirds, 3 + thirds).astype(int)
        regimes = pd.Series(classes, index=px.index, name="regime")

        # Measurement-matched posterior. Side mass = measured side accuracy;
        # within the called side it splits by the measured third|side (0.372
        # on the called third, the rest even) — so the TOP-class mass is
        # a_side * third|side ~= 0.25, matching the measured exact6 instead of
        # overstating it. The off side splits its mass evenly. This is the one
        # probability vector whose largest entry equals what it actually hits.
        a = float(MEASURED_PANEL_SIDE_ACCURACY)
        t3 = float(MEASURED_THIRD_GIVEN_SIDE)
        called_third_mass = a * t3
        other_third_mass = a * (1.0 - t3) / 2.0
        off_side_mass = (1.0 - a) / 3.0

        proba = np.full((len(px), N_CLASSES), off_side_mass)
        side_base = np.where(classes < 3, 0, 3)  # first class of the called side
        for j in range(3):
            proba[np.arange(len(px)), side_base + j] = other_third_mass
        proba[np.arange(len(px)), classes] = called_third_mass
        proba = proba / proba.sum(axis=1, keepdims=True)
        return regimes, pd.DataFrame(proba, index=px.index, columns=_COLS)

    def fit_predict(
        self, prices_train: pd.Series, prices_all: pd.Series
    ) -> tuple[pd.Series, pd.DataFrame]:
        self.fit(prices_train)
        return self.predict(prices_all)
