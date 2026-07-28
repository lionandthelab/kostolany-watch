"""Pooled cross-asset research lane tests."""

from __future__ import annotations

import numpy as np

from kostolany.data import make_synthetic
from kostolany.engine import prepare_xy
from kostolany.harness.pooled_forecast import PooledDirectModel, _tabular_design


def _synthetic_data(seed: int):
    market, _ = make_synthetic(n=1050, seed=seed)
    X, y, _, prices = prepare_xy(market)
    valid = X.dropna().index.intersection(y.dropna().index)
    return X.loc[valid], prices.loc[valid]


def _synthetic_design(seed: int):
    X, prices = _synthetic_data(seed)
    return _tabular_design(X, f"SYNTH-{seed}"), prices


def test_pooled_model_fits_multiple_causal_assets():
    panel = {
        "SYNTH-1": _synthetic_design(21),
        "SYNTH-2": _synthetic_design(22),
    }
    cutoff = panel["SYNTH-1"][0].index[-1]
    model = PooledDirectModel(n_estimators=40).fit(panel, cutoff)
    row = panel["SYNTH-1"][0].dropna().iloc[[-1]]
    median, low, high, p_up = model.predict(row)
    assert np.isfinite([median, low, high, p_up]).all()
    assert low <= median <= high
    assert 0 <= p_up <= 1


def test_pooled_design_is_causal_to_future_perturbation():
    X, _ = _synthetic_data(23)
    raw = _tabular_design(X, "SYNTH-23")
    mutated = X.copy()
    mutated.iloc[700:] = mutated.iloc[700:] * -5.0
    changed = _tabular_design(mutated, "SYNTH-23")
    assert np.allclose(
        raw.iloc[:700].to_numpy(),
        changed.iloc[:700].to_numpy(),
        equal_nan=True,
    )
