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

# Panel-measured side accuracy of the family median (12 instruments,
# side_panel_20260730T025704Z.json). Used ONLY to bound the displayed
# probability mass; re-measure and update alongside calibration.py.
MEASURED_PANEL_SIDE_ACCURACY = 0.6713


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

    def _side_up(self, prices: pd.Series) -> pd.Series:
        px = prices.astype(float)
        votes = []
        for w in MA_WINDOWS:
            votes.append((px > px.rolling(w, min_periods=w // 2).mean()).astype(int))
        for h in RET_HORIZONS:
            votes.append((px.pct_change(h) > 0).astype(int))
        total = sum(votes)
        return total >= (len(votes) / 2.0)

    def predict(self, prices: pd.Series) -> tuple[pd.Series, pd.DataFrame]:
        px = prices.astype(float)
        up = self._side_up(px)
        clock = pit_state(px, min_cycle=self.min_cycle)
        thirds = clock_third(clock["k"], self.cuts_)
        classes = np.where(up.to_numpy(), thirds, 3 + thirds).astype(int)
        regimes = pd.Series(classes, index=px.index, name="regime")

        # Uniform-anchored posterior at the measured accuracy: the called class
        # carries a * (1) + (1-a) * (1/6); the rest carry (1-a)/6 each — the
        # displayed mass equals the measured hit rate, never model confidence.
        a = float(MEASURED_PANEL_SIDE_ACCURACY)
        base = (1.0 - a) / N_CLASSES
        proba = np.full((len(px), N_CLASSES), base)
        proba[np.arange(len(px)), classes] += a
        return regimes, pd.DataFrame(proba, index=px.index, columns=_COLS)

    def fit_predict(
        self, prices_train: pd.Series, prices_all: pd.Series
    ) -> tuple[pd.Series, pd.DataFrame]:
        self.fit(prices_train)
        return self.predict(prices_all)
