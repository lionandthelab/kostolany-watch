"""Connectors, replay, and TSFM v3 tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kostolany.connectors.fred import fred_to_extras
from kostolany.connectors import merge_extras
from kostolany.data import make_synthetic
from kostolany.engine import KostolanyEngine, fit_analyst_bundle, prepare_xy
from kostolany.models import KostolanyHMM, causal_cycle_filter
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
    assert {"h1", "h5", "h20", "h63"}.issubset(traj.ret_hat.columns)
    assert traj.transition_score.between(0, 1).all()
    assert traj.quantiles is not None
    assert (traj.quantiles["h63_q10"] <= traj.quantiles["h63_q90"]).all()
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


def test_shared_analyst_bundle_reuses_ensemble_arms():
    bundle = fit_analyst_bundle("SYNTH")
    # momo joined the bundle when it became the default head (2026-07-30)
    assert set(bundle) == {"momo", "hmm", "gbm", "tsfm"}
    assert bundle["hmm"].model is bundle["tsfm"].model.hmm
    assert bundle["gbm"].model is bundle["tsfm"].model.gbm
    assert all(bundle[k].snapshot().regime in {"A1", "A2", "A3", "B1", "B2", "B3"} for k in bundle)


def test_build_replay_from_pred():
    eng = KostolanyEngine(model_kind="tsfm")
    eng.fit_synthetic(n=500, seed=8)
    assert eng._last_pred is not None and eng._last_features is not None
    frames = build_replay(eng._last_pred, eng._last_features, limit=30)
    assert len(frames) <= 30
    assert frames[-1].regime in {"A1", "A2", "A3", "B1", "B2", "B3"}


def test_direct_forecast_is_causal_to_future_input_perturbation():
    market, _ = make_synthetic(n=900, seed=13)
    X, y, _, _ = prepare_xy(market)
    valid = X.dropna().index.intersection(y.dropna().index)
    X = X.loc[valid]
    cut = 520
    model = LocalTSFM(n_estimators=60)
    model.fit(X.iloc[:cut])

    segment = X.iloc[cut : cut + 150].copy()
    before = model.predict_trajectory(segment).ret_hat
    mutated = segment.copy()
    mutated.iloc[90:] = mutated.iloc[90:] * -7.0 + 3.0
    after = model.predict_trajectory(mutated).ret_hat

    # A causal patch ending before row 90 cannot use changed future rows.
    assert np.allclose(
        before.iloc[:90].to_numpy(),
        after.iloc[:90].to_numpy(),
        atol=1e-10,
    )


def test_h63_target_ends_inside_training_slice_and_matches_price_return():
    market, _ = make_synthetic(n=900, seed=14)
    X, y, _, prices = prepare_xy(market)
    valid = X.dropna().index.intersection(y.dropna().index)
    X, prices = X.loc[valid], prices.loc[valid]
    train = X.iloc[:600]
    h = 63
    target = train["log_ret_1"].rolling(h, min_periods=h).sum().shift(-h)
    last = target.last_valid_index()
    assert last == train.index[-1 - h]
    pos = train.index.get_loc(last)
    realized = float(prices.iloc[pos + h] / prices.iloc[pos] - 1.0)
    assert np.isclose(float(np.expm1(target.loc[last])), realized, atol=1e-12)


def test_cycle_filter_is_causal_and_normalized():
    idx = pd.bdate_range("2024-01-01", periods=30)
    rng = np.random.default_rng(9)
    raw = rng.dirichlet(np.ones(6), size=len(idx))
    proba = pd.DataFrame(raw, index=idx, columns=[f"p{i}" for i in range(6)])
    before = causal_cycle_filter(proba)
    mutated = proba.copy()
    mutated.iloc[20:] = np.roll(mutated.iloc[20:].to_numpy(), 2, axis=1)
    after = causal_cycle_filter(mutated)
    assert np.allclose(before.iloc[:20], after.iloc[:20], atol=1e-12)
    assert np.allclose(before.sum(axis=1), 1.0, atol=1e-12)


def test_hmm_posterior_is_forward_only():
    market, _ = make_synthetic(n=850, seed=15)
    X, y, _, _ = prepare_xy(market)
    valid = X.dropna().index.intersection(y.dropna().index)
    X, y = X.loc[valid], y.loc[valid]
    model = KostolanyHMM(n_iter=80).fit(X.iloc[:500], y.iloc[:500])
    segment = X.iloc[500:650].copy()
    before = model.predict(segment).proba
    mutated = segment.copy()
    mutated.iloc[80:] = mutated.iloc[80:] * -5.0 + 2.0
    after = model.predict(mutated).proba
    assert np.allclose(before.iloc[:80], after.iloc[:80], atol=1e-10)
