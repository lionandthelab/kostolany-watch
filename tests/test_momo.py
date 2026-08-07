"""MomoFloorHead: zero fitted parameters, causal, engine-servable."""

import numpy as np
import pandas as pd

from kostolany.engine import KostolanyEngine
from kostolany.labels_pit import pit_state
from kostolany.momo import (
    MA_WINDOWS,
    MEASURED_PANEL_SIDE_ACCURACY,
    MEASURED_THIRD_GIVEN_SIDE,
    RET_HORIZONS,
    RULE_IDS,
    MomoFloorHead,
)

# Measurement-matched top mass: side accuracy x third|side (~0.25) — the
# largest displayed probability must equal what the head actually hits.
TOP_MASS = MEASURED_PANEL_SIDE_ACCURACY * MEASURED_THIRD_GIVEN_SIDE


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
    top = proba.to_numpy().max(axis=1)
    assert np.allclose(top, TOP_MASS, atol=1e-9)
    # Called-side mass equals the measured side accuracy exactly.
    up_mass = proba.to_numpy()[:, :3].sum(axis=1)
    called_up = (regimes.to_numpy() < 3)
    side_mass = np.where(called_up, up_mass, 1 - up_mass)
    assert np.allclose(side_mass, MEASURED_PANEL_SIDE_ACCURACY, atol=1e-9)


def test_causal_last_bar_perturbation():
    px = _px()
    head = MomoFloorHead().fit(px.iloc[:600])
    r1, _ = head.predict(px)
    bumped = px.copy()
    bumped.iloc[-1] *= 1.3
    r2, _ = head.predict(bumped)
    pd.testing.assert_series_equal(r1.iloc[:-1], r2.iloc[:-1])


def _flip_ladder(head, px):
    """Called-side rules ordered by how little the close had to move, as engine._flip_block does."""
    levels = head.rule_flip_levels(px)
    close = float(px.iloc[-1])
    votes = head.rule_votes(px).iloc[-1]
    side_up = int(votes.sum()) >= 4
    rungs = [(rid, float(levels[rid])) for rid in RULE_IDS if bool(votes[rid]) == side_up]
    return sorted(rungs, key=lambda r: abs(r[1] / close - 1.0)), side_up, int(votes.sum())


def test_rule_flip_levels_reproduce_every_served_vote():
    """The closed form is an identity with `rule_votes`, not an approximation."""
    px = _px(n=900, seed=7)
    head = MomoFloorHead().fit(px)
    levels = head.rule_flip_levels(px)
    assert list(levels.index) == list(RULE_IDS)

    close = float(px.iloc[-1])
    served = head.rule_votes(px).iloc[-1]
    for rid in RULE_IDS:
        assert bool(served[rid]) == (close > float(levels[rid])), rid


def test_rule_votes_flip_across_each_boundary_from_both_sides():
    """Substitute the boundary +/- eps for today's close and re-run the real rule."""
    px = _px(n=900, seed=7)
    head = MomoFloorHead().fit(px)
    levels = head.rule_flip_levels(px)

    for rid in RULE_IDS:
        level = float(levels[rid])
        for eps, expected in ((1e-6, True), (-1e-6, False)):
            bumped = px.copy()
            bumped.iloc[-1] = level * (1.0 + eps)
            assert bool(head.rule_votes(bumped).iloc[-1][rid]) is expected, (rid, eps)


def test_flip_ladder_is_monotone_no_rule_comes_back():
    """Crossing the k-th boundary costs exactly k votes — the rung ordering is unique."""
    px = _px(n=900, seed=7)
    head = MomoFloorHead().fit(px)
    ladder, side_up, up0 = _flip_ladder(head, px)

    for k, (_, level) in enumerate(ladder, start=1):
        bumped = px.copy()
        # Step just PAST the k-th boundary, away from the called side.
        bumped.iloc[-1] = level * (1.0 - 1e-6) if side_up else level * (1.0 + 1e-6)
        up = int(head.rule_votes(bumped).iloc[-1].sum())
        assert up == (up0 - k if side_up else up0 + k), k


def test_side_flip_rung_is_the_one_that_moves_the_majority():
    px = _px(n=900, seed=7)
    head = MomoFloorHead().fit(px)
    ladder, side_up, up0 = _flip_ladder(head, px)
    need = up0 - 3 if side_up else 4 - up0
    assert 1 <= need <= len(ladder)  # the called side always has enough rules

    regimes, _ = head.predict(px)
    level = ladder[need - 1][1]
    bumped = px.copy()
    bumped.iloc[-1] = level * (1.0 - 1e-6) if side_up else level * (1.0 + 1e-6)
    flipped, _ = head.predict(bumped)
    assert (int(flipped.iloc[-1]) < 3) is not (int(regimes.iloc[-1]) < 3)

    # One rung short of that is NOT enough to move the side.
    if need > 1:
        level = ladder[need - 2][1]
        held = px.copy()
        held.iloc[-1] = level * (1.0 - 1e-6) if side_up else level * (1.0 + 1e-6)
        still, _ = head.predict(held)
        assert (int(still.iloc[-1]) < 3) is (int(regimes.iloc[-1]) < 3)


def test_counterfactual_close_leaves_the_turn_clock_untouched():
    """pit_state's confirmation window ends at t-1, so the flipped call keeps its third."""
    px = _px(n=900, seed=7)
    bumped = px.copy()
    bumped.iloc[-1] *= 0.85
    pd.testing.assert_frame_equal(pit_state(px), pit_state(bumped))

    head = MomoFloorHead().fit(px)
    before, _ = head.predict(px)
    after, _ = head.predict(bumped)
    r0, r1 = int(before.iloc[-1]), int(after.iloc[-1])
    assert r0 % 3 == r1 % 3  # same sector number, only the side may have moved


def test_short_history_ships_no_flip_levels():
    """Half-full rolling windows would put the served vote on a different mean."""
    assert MomoFloorHead().rule_flip_levels(_px(n=max(MA_WINDOWS))).empty
    assert MomoFloorHead().rule_flip_levels(_px(n=max(RET_HORIZONS))).empty
    assert not MomoFloorHead().rule_flip_levels(_px(n=max(MA_WINDOWS) + 1)).empty


def test_engine_serves_momo_kind():
    eng = KostolanyEngine(model_kind="momo")
    eng.fit_synthetic(n=900, seed=3)
    snap = eng.snapshot()
    assert snap.regime in {"A1", "A2", "A3", "B1", "B2", "B3"}
    assert abs(sum(snap.probabilities.values()) - 1.0) < 1e-6
    # Confidence equals the measurement-matched top mass, not model confidence.
    assert abs(snap.confidence - TOP_MASS) < 1e-6
    assert snap.disclaimer
