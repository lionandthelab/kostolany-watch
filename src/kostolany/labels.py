"""Hybrid labeling: weak (train) + gold (eval-only) + HMM state mapping helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kostolany.features import gauge_scores
from kostolany.regimes import Regime


def weak_labels(features: pd.DataFrame) -> pd.Series:
    """Rule-based weak supervision from Kostolany axes.

    Uses only contemporaneous/causal features — safe for training.
    Scoring is relative (rank/quantile within a causal rolling window) so
    thresholds adapt across assets and eras.
    """
    g = gauge_scores(features)
    trend = features["trend_slope"].fillna(0.0)
    dd = features["drawdown"].fillna(0.0)
    vol = g["volume"].fillna(0.5)
    part = g["participation"].fillna(0.5)
    sent = g["sentiment"].fillna(0.5)
    surge = features["vol_surge"].fillna(1.0)

    # Causal rolling ranks in [0, 1]
    def _rank(s: pd.Series, win: int = 126) -> pd.Series:
        return s.rolling(win, min_periods=30).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
        )

    r_vol = _rank(vol).fillna(0.5)
    r_sent = _rank(sent).fillna(0.5)
    r_part = _rank(part).fillna(0.5)
    r_surge = _rank(surge).fillna(0.5)
    r_trend = _rank(trend).fillna(0.5)

    # Score each regime prototype (higher = better match)
    scores = pd.DataFrame(
        {
            int(Regime.A1): (
                (1 - r_vol) * 0.25
                + (1 - r_sent) * 0.2
                + (1 - r_surge) * 0.15
                + (r_trend.clip(0, 0.6) / 0.6) * 0.15
                + (-dd.clip(-0.4, 0) / 0.4) * 0.25
            ),
            int(Regime.A2): (
                r_trend * 0.35
                + r_part * 0.25
                + r_vol * 0.2
                + r_sent * 0.2
            ),
            int(Regime.A3): (
                r_trend * 0.25
                + r_vol * 0.25
                + r_surge * 0.2
                + r_sent * 0.25
                + (1 + dd.clip(-0.15, 0) / 0.15).clip(0, 1) * 0.05
            ),
            int(Regime.B1): (
                (1 - r_trend) * 0.25
                + r_sent * 0.25
                + (1 - r_vol) * 0.2
                + r_part * 0.15
                + (1 + dd.clip(-0.12, 0)).clip(0, 1) * 0.15
            ),
            int(Regime.B2): (
                (1 - r_trend) * 0.35
                + r_vol * 0.25
                + (1 - r_part) * 0.2
                + (1 - r_sent) * 0.2
            ),
            int(Regime.B3): (
                (1 - r_trend) * 0.25
                + r_vol * 0.25
                + r_surge * 0.2
                + (1 - r_sent) * 0.25
                + (-dd.clip(-0.5, 0) / 0.5) * 0.05
            ),
        },
        index=features.index,
    )

    labels = scores.idxmax(axis=1).astype(int)
    return _causal_mode(labels, window=5)


def _causal_mode(s: pd.Series, window: int = 5) -> pd.Series:
    vals = s.to_numpy()
    out = np.empty_like(vals)
    for i in range(len(vals)):
        lo = max(0, i - window + 1)
        chunk = vals[lo : i + 1]
        out[i] = int(np.bincount(chunk).argmax())
    return pd.Series(out, index=s.index, name="weak_label")


def gold_labels(prices: pd.Series, *, min_cycle: int = 60) -> pd.Series:
    """Post-hoc cycle segmentation into 6 regimes — EVAL ONLY.

    Identifies major peaks/troughs then trisects each up/down leg.
    Uses future information by construction → must never train on this.
    """
    px = prices.astype(float).sort_index()
    win = max(20, min_cycle // 3)
    roll_max = px.rolling(win, center=True, min_periods=5).max()
    roll_min = px.rolling(win, center=True, min_periods=5).min()
    is_peak = (px == roll_max) & (px.shift(1) < px) & (px.shift(-1) < px)
    is_trough = (px == roll_min) & (px.shift(1) > px) & (px.shift(-1) > px)

    turns = sorted(
        [(i, "peak") for i in px.index[is_peak.fillna(False)]]
        + [(i, "trough") for i in px.index[is_trough.fillna(False)]]
    )
    filtered: list[tuple[pd.Timestamp, str]] = []
    for t, kind in turns:
        if not filtered:
            filtered.append((t, kind))
            continue
        prev_t, prev_k = filtered[-1]
        if kind == prev_k:
            if kind == "peak" and px.loc[t] >= px.loc[prev_t]:
                filtered[-1] = (t, kind)
            elif kind == "trough" and px.loc[t] <= px.loc[prev_t]:
                filtered[-1] = (t, kind)
            continue
        delta = t - prev_t
        gap = delta.days if hasattr(delta, "days") else int(delta)
        if gap < min_cycle // 2:
            continue
        filtered.append((t, kind))

    labels = pd.Series(index=px.index, dtype=float)
    if len(filtered) < 2:
        n = len(px)
        third = max(n // 3, 1)
        labels.iloc[:third] = int(Regime.A1)
        labels.iloc[third : 2 * third] = int(Regime.A2)
        labels.iloc[2 * third :] = int(Regime.A3)
        return labels.astype(int).rename("gold_label")

    for (t0, k0), (t1, k1) in zip(filtered[:-1], filtered[1:]):
        seg = px.loc[t0:t1]
        if len(seg) < 6:
            continue
        cuts = np.array_split(seg.index.to_numpy(), 3)
        if k0 == "trough" and k1 == "peak":
            phases = [Regime.A1, Regime.A2, Regime.A3]
        elif k0 == "peak" and k1 == "trough":
            phases = [Regime.B1, Regime.B2, Regime.B3]
        else:
            continue
        for idx, phase in zip(cuts, phases):
            labels.loc[idx] = int(phase)

    labels = labels.ffill().bfill()
    return labels.astype(int).rename("gold_label")


def map_hmm_states_to_regimes(
    states: np.ndarray,
    features: pd.DataFrame,
    n_states: int,
) -> dict[int, int]:
    """Map anonymous HMM states → Kostolany regimes via mean feature signatures."""
    g = gauge_scores(features)
    tmp = pd.DataFrame(
        {
            "state": states,
            "trend": features["trend_slope"].fillna(0).to_numpy(),
            "vol": g["volume"].to_numpy(),
            "part": g["participation"].to_numpy(),
            "sent": g["sentiment"].to_numpy(),
            "dd": features["drawdown"].fillna(0).to_numpy(),
        },
        index=features.index,
    )
    profiles = tmp.groupby("state").mean()

    prototypes = {
        Regime.A1: dict(trend=0.1, vol=0.3, part=0.3, sent=0.35, dd=-0.25),
        Regime.A2: dict(trend=0.6, vol=0.55, part=0.55, sent=0.55, dd=-0.08),
        Regime.A3: dict(trend=0.9, vol=0.8, part=0.75, sent=0.8, dd=-0.02),
        Regime.B1: dict(trend=-0.2, vol=0.35, part=0.45, sent=0.55, dd=-0.05),
        Regime.B2: dict(trend=-0.6, vol=0.55, part=0.35, sent=0.35, dd=-0.15),
        Regime.B3: dict(trend=-0.9, vol=0.85, part=0.2, sent=0.15, dd=-0.35),
    }

    mapping: dict[int, int] = {}
    used: set[int] = set()
    scores: list[tuple[float, int, int]] = []
    for state in profiles.index:
        row = profiles.loc[state]
        for regime, proto in prototypes.items():
            dist = sum((float(row[k]) - v) ** 2 for k, v in proto.items())
            scores.append((dist, int(state), int(regime)))
    scores.sort()
    for dist, state, regime in scores:
        if state in mapping or regime in used:
            continue
        mapping[state] = regime
        used.add(regime)
        if len(mapping) == n_states:
            break
    for state in range(n_states):
        if state in mapping:
            continue
        row = profiles.loc[state] if state in profiles.index else None
        if row is None:
            mapping[state] = int(Regime.A2)
            continue
        best_r, best_d = int(Regime.A2), 1e9
        for regime, proto in prototypes.items():
            dist = sum((float(row[k]) - v) ** 2 for k, v in proto.items())
            if dist < best_d:
                best_d, best_r = dist, int(regime)
        mapping[state] = best_r
    return mapping
