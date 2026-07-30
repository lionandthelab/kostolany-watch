"""pit_state: causality, prefix stability (G13), and clock sanity."""

import numpy as np
import pandas as pd

from kostolany.anchor import apply_lambda, fit_lambda
from kostolany.labels_pit import clock_terciles, clock_third, pit_state


def _px(n: int = 900, seed: int = 11) -> pd.Series:
    rng = np.random.default_rng(seed)
    # Cyclic drift + noise so confirmed peaks/troughs actually occur.
    t = np.arange(n)
    drift = 0.0015 * np.sin(2 * np.pi * t / 120)
    ret = drift + rng.normal(0, 0.01, n)
    return pd.Series(
        100 * np.exp(np.cumsum(ret)), index=pd.bdate_range("2018-01-02", periods=n)
    )


def test_pit_state_is_causal_and_prefix_stable():
    px = _px()
    full = pit_state(px)
    rng = np.random.default_rng(3)
    # G13: for random prefixes, the state computed on the prefix must equal the
    # state computed on the full series restricted to that prefix.
    for cut in rng.integers(100, len(px), size=60):
        prefix = pit_state(px.iloc[:cut])
        pd.testing.assert_frame_equal(prefix, full.iloc[:cut])


def test_pit_state_never_reads_the_future():
    px = _px()
    base = pit_state(px)
    # Perturb ONLY the last bar violently; nothing before index -1 may change.
    bumped = px.copy()
    bumped.iloc[-1] *= 1.5
    pert = pit_state(bumped)
    pd.testing.assert_frame_equal(base.iloc[:-1], pert.iloc[:-1])


def test_pit_state_alternates_and_k_counts_up():
    px = _px()
    st = pit_state(px)
    active = st[st["side"] != 0]
    assert len(active) > 300
    # k increments by exactly 1 wherever the side did not change.
    same = active["side"].diff().fillna(0) == 0
    dk = active["k"].diff().fillna(1)
    assert (dk[same] == 1).mean() > 0.95  # re-anchoring same-kind turns may reset


def test_clock_terciles_and_third():
    k = pd.Series(np.arange(0, 90))
    cuts = clock_terciles(k)
    third = clock_third(k, cuts)
    counts = np.bincount(third, minlength=3)
    assert counts.min() > 20  # roughly balanced by construction


def test_lambda_anchor_grid_behaviour():
    rng = np.random.default_rng(5)
    n = 400
    y = pd.Series(rng.integers(0, 6, n))
    # Perfectly informative probabilities -> lambda should go to 1.
    hot = pd.DataFrame(np.eye(6)[y.to_numpy()] * 0.94 + 0.01, columns=[f"p{i}" for i in range(6)])
    assert fit_lambda(hot, y) > 0.9
    # Anti-informative probabilities -> lambda pinned at 0 (uniform floor).
    anti = pd.DataFrame(0.2 * (1 - np.eye(6)[y.to_numpy()]) + 1e-6, columns=hot.columns)
    assert fit_lambda(anti, y) == 0.0
    out = apply_lambda(hot, 0.0)
    assert np.allclose(out.to_numpy(), 1.0 / 6.0)
    out1 = apply_lambda(hot, 1.0)
    assert np.allclose(out1.sum(axis=1), 1.0)
