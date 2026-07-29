"""Continuous phase head tests — attribution identity, calibration, causality, gold isolation."""

from __future__ import annotations

import math

import numpy as np
import pytest

from kostolany.data import make_synthetic
from kostolany.engine import prepare_xy
from kostolany.labels import gold_labels
from kostolany.phase import (
    DEFAULT_PHASE_FEATURES,
    PhaseHead,
    gold_leg_segments,
    gold_phase_angle,
    gold_phase_sectors,
    mardia_kappa,
    sector_probabilities,
    weak_phase_angle,
    wrap_pi,
)

TWO_PI = 2.0 * math.pi


@pytest.fixture(scope="module")
def frame():
    market, _ = make_synthetic(n=1600, seed=11)
    X, y_weak, _y_gold, prices = prepare_xy(market)
    valid = (
        X.dropna().index.intersection(y_weak.dropna().index).intersection(prices.dropna().index)
    )
    return X.loc[valid], y_weak.loc[valid].astype(int), prices.loc[valid].astype(float)


@pytest.fixture(scope="module")
def fitted(frame):
    X, y, _ = frame
    return PhaseHead().fit(X, y), X, y


def test_attribution_contributions_sum_to_theta_minus_intercept(fitted):
    head, X, _ = fitted
    for pos in (0, len(X) // 3, len(X) - 1):
        attr = head.attribution(X.iloc[[pos]])
        total = sum(attr["features"].values())
        # Circular identity: the wrapped increments telescope exactly.
        residual = float(wrap_pi(total - (attr["theta"] - attr["theta_intercept"])))
        assert abs(residual) < 1e-9, (pos, residual)
        # Group rollup must preserve the same total.
        assert abs(sum(attr["groups"].values()) - total) < 1e-12
        assert set(attr["groups"]).issubset(
            {"volume", "participation", "money", "sentiment", "position"}
        )


def test_attribution_reproduces_predicted_theta(fitted):
    head, X, _ = fitted
    row = X.iloc[[-1]]
    attr = head.attribution(row)
    theta = float(head.predict(row).theta.iloc[0])
    assert abs(float(wrap_pi(attr["theta"] - theta))) < 1e-9


def test_probabilities_sum_to_one_and_are_strictly_positive(fitted):
    head, X, _ = fitted
    pred = head.predict(X)
    arr = pred.proba.to_numpy()
    assert arr.shape[1] == 6
    np.testing.assert_allclose(arr.sum(axis=1), 1.0, atol=1e-12)
    # Guards the structurally-zero-class bug the HMM mapping has: every regime
    # must keep a non-zero probability on every row.
    assert (arr > 0.0).all()
    assert (pred.proba.min(axis=0) > 0.0).all()


def test_probabilities_positive_even_at_extreme_concentration():
    theta = np.linspace(0.0, TWO_PI, 25, endpoint=False)
    p = sector_probabilities(theta, 12.0)
    np.testing.assert_allclose(p.sum(axis=1), 1.0, atol=1e-12)
    assert (p > 0.0).all()


def test_kappa_is_finite_and_positive(fitted):
    head, X, _ = fitted
    assert np.isfinite(head.kappa_)
    assert head.kappa_ > 0.0
    assert head.n_calibration_ == 252
    assert head.calibration_in_sample_ is False
    assert head.kappa_ == head.predict(X.iloc[[-1]]).kappa
    # Estimator edges stay finite and positive.
    assert mardia_kappa(np.random.default_rng(0).uniform(-math.pi, math.pi, 500)) > 0.0
    assert np.isfinite(mardia_kappa(np.zeros(500)))
    assert mardia_kappa(np.zeros(500)) > 0.0


def test_prediction_is_causal_to_future_perturbation(frame):
    """Mirrors tests/test_pooled_forecast.py: mutating future bars must not move
    the prediction of a head that was only shown data up to the cut."""
    X, y, _ = frame
    cut = 900
    head = PhaseHead().fit(X.iloc[:cut], y.iloc[:cut])
    base = head.predict(X)

    mutated = X.copy()
    mutated.iloc[cut:] = mutated.iloc[cut:] * -5.0 + 3.0
    moved = head.predict(mutated)

    np.testing.assert_allclose(
        base.theta.iloc[:cut].to_numpy(), moved.theta.iloc[:cut].to_numpy(), atol=0.0
    )
    np.testing.assert_allclose(
        base.proba.iloc[:cut].to_numpy(), moved.proba.iloc[:cut].to_numpy(), atol=0.0
    )
    # The fit itself only ever sees the prefix, so kappa and the coefficients
    # are identical too.
    refit = PhaseHead().fit(mutated.iloc[:cut], y.iloc[:cut])
    assert refit.kappa_ == head.kappa_
    np.testing.assert_allclose(refit.coef_sin_, head.coef_sin_, atol=0.0)
    np.testing.assert_allclose(refit.coef_cos_, head.coef_cos_, atol=0.0)


def test_phase_head_never_touches_gold(frame):
    """A corrupted gold column sitting in the frame must change nothing."""
    X, y, prices = frame
    poisoned = X.copy()
    rng = np.random.default_rng(7)
    poisoned["gold_label"] = rng.integers(0, 6, size=len(X))
    poisoned["gold_phase_angle"] = gold_phase_angle(prices).to_numpy() * -13.0
    poisoned["y_gold"] = gold_labels(prices).to_numpy()[::-1]

    clean_head = PhaseHead().fit(X, y)
    poisoned_head = PhaseHead().fit(poisoned, y)

    assert poisoned_head.columns_ == clean_head.columns_
    assert "gold_label" not in poisoned_head.columns_
    np.testing.assert_allclose(poisoned_head.coef_sin_, clean_head.coef_sin_, atol=0.0)
    np.testing.assert_allclose(poisoned_head.coef_cos_, clean_head.coef_cos_, atol=0.0)
    assert poisoned_head.kappa_ == clean_head.kappa_
    np.testing.assert_allclose(
        poisoned_head.predict(poisoned).theta.to_numpy(),
        clean_head.predict(X).theta.to_numpy(),
        atol=0.0,
    )


def test_training_target_is_the_weak_label_angle(frame):
    X, y, _ = frame
    theta = weak_phase_angle(y)
    assert theta.min() >= 0.0 and theta.max() < TWO_PI
    # Sector centres: class c -> (c + 0.5) * pi/3
    np.testing.assert_allclose(
        theta.to_numpy(), (y.to_numpy() + 0.5) * (math.pi / 3.0), atol=1e-12
    )


def test_fit_predict_matches_model_interface(frame):
    X, y, _ = frame
    regimes, proba = PhaseHead().fit_predict(X.iloc[:900], y.iloc[:900], X.iloc[900:])
    assert list(proba.columns) == [f"p{i}" for i in range(6)]
    assert regimes.index.equals(X.iloc[900:].index)
    assert proba.index.equals(X.iloc[900:].index)
    assert regimes.between(0, 5).all()
    # The von Mises mass is centred on theta, so its argmax IS the theta sector.
    np.testing.assert_array_equal(
        regimes.to_numpy(), proba.to_numpy().argmax(axis=1).astype(int)
    )


def test_predicted_sector_is_the_theta_sector(fitted):
    head, X, _ = fitted
    pred = head.predict(X)
    expected = np.clip((pred.theta.to_numpy() // (math.pi / 3.0)).astype(int), 0, 5)
    np.testing.assert_array_equal(pred.regimes.to_numpy(), expected)
    assert (pred.R >= 0.0).all()


def test_gold_phase_angle_reproduces_gold_labels(frame):
    """EVAL-ONLY object must be the same object as gold_labels, not a new one."""
    _X, _y, prices = frame
    sectors = gold_phase_sectors(prices)
    legacy = gold_labels(prices)
    agreement = float((sectors == legacy).mean())
    diff = np.abs(sectors.to_numpy() - legacy.to_numpy())
    cyclic = np.minimum(diff, 6 - diff)
    assert agreement > 0.90, agreement
    assert float(np.mean(cyclic <= 1)) == 1.0

    theta = gold_phase_angle(prices)
    assert theta.min() >= 0.0 and theta.max() < TWO_PI
    segments = gold_leg_segments(prices)
    assert (segments["leg_id"] >= 0).all()
    assert segments["leg_id"].nunique() >= 2
    # u walks 0 -> <1 inside each leg
    grouped = segments.groupby("leg_id")["u"]
    assert float(grouped.min().max()) == 0.0
    assert float(grouped.max().max()) < 1.0


def test_default_features_are_causal_model_matrix_columns(frame):
    X, _y, _prices = frame
    assert set(DEFAULT_PHASE_FEATURES).issubset(set(X.columns))
    assert "log_ret_1" not in DEFAULT_PHASE_FEATURES
