"""MomoFloorHead: zero fitted parameters, causal, engine-servable."""

import numpy as np
import pandas as pd

from kostolany.engine import KostolanyEngine
from kostolany.momo import MEASURED_PANEL_SIDE_ACCURACY, MomoFloorHead


def _px(n: int = 900, seed: int = 4) -> pd.Series:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    ret = 0.0012 * np.sin(2 * np.pi * t / 130) + rng.normal(0, 0.01, n)
    return pd.Series(100 * np.exp(np.cumsum(ret)), index=pd.bdate_range("2019-01-02", periods=n))


def test_predict_shape_and_probability_contract():
    px = _px()
    head = MomoFloorHead().fit(px.iloc[:600])
    regimes, proba = head.predict(px)
    assert len(regimes) == len(px)
    assert np.allclose(proba.sum(axis=1), 1.0)
    # The called class carries exactly measured accuracy + uniform share.
    a = MEASURED_PANEL_SIDE_ACCURACY
    top = proba.to_numpy().max(axis=1)
    assert np.allclose(top, a + (1 - a) / 6.0)


def test_causal_last_bar_perturbation():
    px = _px()
    head = MomoFloorHead().fit(px.iloc[:600])
    r1, _ = head.predict(px)
    bumped = px.copy()
    bumped.iloc[-1] *= 1.3
    r2, _ = head.predict(bumped)
    pd.testing.assert_series_equal(r1.iloc[:-1], r2.iloc[:-1])


def test_engine_serves_momo_kind():
    eng = KostolanyEngine(model_kind="momo")
    eng.fit_synthetic(n=900, seed=3)
    snap = eng.snapshot()
    assert snap.regime in {"A1", "A2", "A3", "B1", "B2", "B3"}
    assert abs(sum(snap.probabilities.values()) - 1.0) < 1e-6
    # Confidence equals the measured-accuracy bound, not a model posterior.
    a = MEASURED_PANEL_SIDE_ACCURACY
    assert abs(snap.confidence - (a + (1 - a) / 6.0)) < 1e-6
    assert snap.disclaimer
