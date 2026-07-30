"""One-scalar uniform anchor for regime probability vectors.

``p_final = (1 - lam) * uniform + lam * p_model`` — the single calibration
knob the SideHead pre-registration allows (side_head_v1.json). ``lam`` is
fitted by log-loss on a CAUSAL holdout (the trailing slice of the training
window, which the model being anchored was not fitted on). At ``lam = 0`` the
output is exactly the uniform floor (Brier 5/36), so the downside of the fit
is bounded by one scalar's estimation error against a target-free constant.

Used in two places that must stay in lock-step:
  - serving: ``engine.fit_analyst_bundle`` anchors the three served arms
  - measurement: ``scripts/run_phase_experiment.py`` scores the anchored arms
"""

from __future__ import annotations

import numpy as np
import pandas as pd

N_CLASSES = 6
LAMBDA_GRID = np.linspace(0.0, 1.0, 41)


def fit_lambda(proba_holdout: pd.DataFrame, y_holdout: pd.Series) -> float:
    """Log-loss-optimal blend weight toward uniform, on a causal holdout.

    ``y_holdout`` is the causal weak label (never gold). Rows with NaN in
    either input are dropped; with fewer than 30 clean rows the anchor
    defaults to the uniform floor (lam = 0) rather than trusting noise.
    """
    P = proba_holdout.to_numpy(dtype=float)
    y = pd.Series(y_holdout).to_numpy()
    mask = np.isfinite(P).all(axis=1) & pd.notna(y)
    P, y = P[mask], y[mask].astype(int)
    if len(y) < 30:
        return 0.0
    P = P / np.clip(P.sum(axis=1, keepdims=True), 1e-12, None)
    p_true = P[np.arange(len(y)), y]
    best_lam, best_loss = 0.0, np.inf
    for lam in LAMBDA_GRID:
        blended = (1.0 - lam) / N_CLASSES + lam * p_true
        loss = float(-np.mean(np.log(np.clip(blended, 1e-12, None))))
        if loss < best_loss:
            best_lam, best_loss = float(lam), loss
    return best_lam


def apply_lambda(proba: pd.DataFrame, lam: float) -> pd.DataFrame:
    """Blend a probability frame toward uniform. Columns/index preserved."""
    lam = float(np.clip(lam, 0.0, 1.0))
    arr = proba.to_numpy(dtype=float)
    arr = arr / np.clip(arr.sum(axis=1, keepdims=True), 1e-12, None)
    out = (1.0 - lam) / N_CLASSES + lam * arr
    return pd.DataFrame(out, index=proba.index, columns=proba.columns)
