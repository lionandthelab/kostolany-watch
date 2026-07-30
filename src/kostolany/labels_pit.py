"""Point-in-time turn clock — the causal analogue of gold's leg segmentation.

``pit_state`` answers, at every bar t using ONLY closes <= t: which side of the
egg are we on (up-leg after a confirmed trough / down-leg after a confirmed
peak), and how many bars have elapsed since that confirmed turn (``k``).

Confirmation reuses gold's own extreme test (labels.py: centered rolling
max/min with strict neighbours) evaluated at ``tau = t - confirm_lag``, so the
furthest bar it ever reads is ``t - 1``. Declaration lag is exactly
``confirm_lag`` bars and a declared state is NEVER revised — appending new data
can only extend the output, not rewrite it (prefix stability, gate G13).

This is the "declared flat" third factor of the SideHead architecture: gold's
within-leg third is floor(3*(t-t0)/(t1-t0)) with t1 a FUTURE turning point, so
no causal estimator can recover it. The clock supplies the elapsed-time prior
(third|side ~ 0.372 vs the 1/3 coin) at zero fitted parameters, and nothing in
this module may ever claim more than that.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = ["pit_state", "clock_terciles", "clock_third"]


def pit_state(
    close: pd.Series,
    *,
    min_cycle: int = 60,
    confirm_lag: int = 10,
) -> pd.DataFrame:
    """Per-bar causal (side, k) from confirmed turning points.

    Columns:
      side  +1 up-leg (last confirmed turn was a trough), -1 down-leg,
            0 during warm-up before the first confirmation.
      k     bars since the last confirmed turn's extreme (-1 during warm-up).

    Same-kind, more-extreme turns re-anchor the clock going FORWARD only;
    opposite-kind turns must clear ``min_cycle // 2`` bars, mirroring gold's
    minimum-gap filter — but applied incrementally, never retroactively.
    """
    px = close.astype(float).sort_index()
    values = px.to_numpy(dtype=float)
    n = len(values)
    side = np.zeros(n, dtype=int)
    k = np.full(n, -1, dtype=int)

    half = int(confirm_lag)
    min_gap = max(1, int(min_cycle) // 2)
    last_pos = -1
    last_kind = 0  # 0 none, +1 trough, -1 peak

    for t in range(n):
        tau = t - half
        # Window [tau-half, tau+half-1] must exist; right edge is t-1 (causal).
        if tau - half >= 0 and tau + 1 < n:
            lo, hi = tau - half, tau + half  # slice end exclusive -> tau+half-1
            window = values[lo:hi]
            v = values[tau]
            kind = 0
            if v >= window.max() and values[tau - 1] < v and values[tau + 1] < v:
                kind = -1  # peak -> down-leg begins
            elif v <= window.min() and values[tau - 1] > v and values[tau + 1] > v:
                kind = +1  # trough -> up-leg begins
            if kind != 0:
                if last_kind == 0:
                    accept = True
                elif kind == last_kind:
                    # Same-kind extension: only if strictly more extreme.
                    if kind == -1:
                        accept = v >= values[last_pos]
                    else:
                        accept = v <= values[last_pos]
                else:
                    accept = (tau - last_pos) >= min_gap
                if accept:
                    last_pos, last_kind = tau, kind
        if last_kind != 0:
            side[t] = 1 if last_kind == +1 else -1
            k[t] = t - last_pos

    return pd.DataFrame({"side": side, "k": k}, index=px.index)


def clock_terciles(k_train: pd.Series | np.ndarray) -> tuple[float, float]:
    """Tercile cuts of elapsed-bars ``k``, fitted on TRAIN rows only."""
    arr = np.asarray(k_train, dtype=float)
    arr = arr[np.isfinite(arr) & (arr >= 0)]
    if arr.size < 30:
        return (10.0, 25.0)  # degenerate warm-up fallback; documented, not tuned
    lo, hi = np.percentile(arr, [100 / 3, 200 / 3])
    return (float(lo), float(hi))


def clock_third(k: pd.Series | np.ndarray, cuts: tuple[float, float]) -> np.ndarray:
    """0/1/2 position-within-leg from elapsed bars and train-fitted cuts."""
    arr = np.asarray(k, dtype=float)
    third = np.where(arr <= cuts[0], 0, np.where(arr <= cuts[1], 1, 2))
    return third.astype(int)
