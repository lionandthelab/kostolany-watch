"""Connectors, replay, and TSFM v3 tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kostolany.connectors.fred import fred_to_extras
from kostolany.connectors import merge_extras
from kostolany.data import make_synthetic
from kostolany.engine import KostolanyEngine, prepare_xy
from kostolany.replay import build_replay
from kostolany.tsfm import LocalTSFM, TSFMEnsemble


def test_fred_to_extras_shapes():
    idx = pd.bdate_range("2020-01-01", periods=200)
    panel = pd.DataFrame(
        {
            "fed_funds": np.linspace(0.5, 5.0, 200),
            "m2": np.linspace(1e4, 1.2e4, 200),
            "yield_curve": np.linspace(1.0, -0.5, 200),
            "vix": np.linspace(12, 35, 200),
            "credit_spread": np.linspace(1.0, 3.0, 200),
        },
        index=idx,
    )
    extras = fred_to_extras(panel)
    assert set(["money_proxy", "credit_proxy", "sentiment_override"]).issubset(extras.columns)
    assert extras["money_proxy"].notna().sum() > 50


def test_merge_extras():
    idx = pd.bdate_range("2020-01-01", periods=10)
    a = pd.DataFrame({"money_proxy": np.arange(10)}, index=idx)
    b = pd.DataFrame({"vix": np.arange(10, 20)}, index=idx)
    m = merge_extras(a, b)
    assert m is not None
    assert "money_proxy" in m.columns and "vix" in m.columns


def test_replay_frames_monotonic():
    eng = KostolanyEngine(model_kind="hmm")
    eng.fit_synthetic(n=600, seed=2)
    frames = eng.replay(limit=80, stride=2)
    assert len(frames) > 10
    dates = [f.date for f in frames]
    assert dates == sorted(dates)
    assert all(-1.5 <= f.egg["x"] <= 1.5 for f in frames)
    payload = eng.replay_dict(limit=40)
    assert payload["n"] == len(payload["frames"])
    assert "disclaimer" in payload


def test_snapshot_asof_replay_point():
    eng = KostolanyEngine(model_kind="hmm")
    eng.fit_synthetic(n=500, seed=4)
    hist = eng.history().dropna()
    mid = str(hist.index[len(hist) // 2].date())
    snap = eng.snapshot(asof=mid)
    assert snap.asof <= mid


def test_local_tsfm_trajectory():
    market, _ = make_synthetic(n=700, seed=5)
    X, y, _, _ = prepare_xy(market)
    valid = X.dropna().index.intersection(y.dropna().index)
    X, y = X.loc[valid], y.loc[valid]
    mid = len(X) // 2
    tsfm = LocalTSFM()
    tsfm.fit(X.iloc[:mid])
    traj = tsfm.predict_trajectory(X.iloc[mid:])
    assert len(traj.ret_hat) == len(X.iloc[mid:])
    assert traj.transition_score.between(0, 1).all()
    proba = tsfm.regime_proba_from_trajectory(traj)
    assert np.allclose(proba.sum(axis=1), 1.0, atol=1e-5)


def test_tsfm_ensemble_fit_predict():
    market, _ = make_synthetic(n=700, seed=6)
    X, y, _, _ = prepare_xy(market)
    valid = X.dropna().index.intersection(y.dropna().index)
    X, y = X.loc[valid], y.loc[valid]
    mid = len(X) // 2
    model = TSFMEnsemble()
    pred, proba = model.fit_predict(X.iloc[:mid], y.iloc[:mid], X.iloc[mid:])
    assert pred.notna().sum() > 0
    assert proba.shape[1] == 6
    assert model.last_traj_ is not None


def test_build_replay_from_pred():
    eng = KostolanyEngine(model_kind="tsfm")
    eng.fit_synthetic(n=500, seed=8)
    assert eng._last_pred is not None and eng._last_features is not None
    frames = build_replay(eng._last_pred, eng._last_features, limit=30)
    assert len(frames) <= 30
    assert frames[-1].regime in {"A1", "A2", "A3", "B1", "B2", "B3"}
