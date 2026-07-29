"""Harness unit tests — leakage, CV integrity, metrics, backtest lag."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kostolany.data import make_synthetic
from kostolany.engine import prepare_xy
from kostolany.features import build_features, model_matrix
from kostolany.harness.backtest import BacktestConfig, economic_backtest
from kostolany.harness.commercial_eval import (
    GATE_THRESHOLDS,
    bootstrap_block_for_stride,
    build_gates,
)
from kostolany.harness.cv import CombinatorialPurgedCV, PurgedWalkForward
from kostolany.harness.leakage import LeakageAuditor
from kostolany.harness.metrics import (
    evaluate_regimes,
    evaluate_transitions,
    quadratic_weighted_kappa,
    regime_baselines,
)
from kostolany.harness.runner import ExperimentConfig, ExperimentRunner
from kostolany.labels import gold_labels, weak_labels
from kostolany.models import KostolanyHMM


@pytest.fixture(scope="module")
def synth():
    market, planted = make_synthetic(n=1200, seed=0)
    return market, planted


def test_cpcv_no_train_test_overlap():
    n = 600
    X = pd.DataFrame({"a": np.arange(n)})
    cv = CombinatorialPurgedCV(n_groups=6, n_test_groups=2, purge_horizon=5, embargo_pct=0.02)
    for fold in cv.split(X):
        inter = set(fold.train_idx) & set(fold.test_idx)
        assert not inter
        assert len(fold.train_idx) > 0 and len(fold.test_idx) > 0


def test_walkforward_respects_purge_gap():
    n = 500
    X = pd.DataFrame({"a": np.arange(n)})
    wf = PurgedWalkForward(n_splits=3, purge_horizon=10, embargo=5, min_train_size=120, test_size=80)
    for fold in wf.split(X):
        assert fold.train_idx.max() < fold.test_idx.min()
        gap = fold.test_idx.min() - fold.train_idx.max()
        assert gap >= 1


def test_walkforward_default_anchor_tests_the_tail():
    """anchor='end' places the last block on the final row (A1)."""
    n = 1000
    X = pd.DataFrame({"a": np.arange(n)})
    wf = PurgedWalkForward(
        n_splits=5, test_size=50, min_train_size=200, purge_horizon=5, embargo=5
    )
    folds = list(wf.split(X))
    assert wf.anchor == "end"
    assert len(folds) == 5
    covered = np.concatenate([f.test_idx for f in folds])
    assert covered.min() == n - 5 * 50
    assert covered.max() == n - 1
    assert len(np.unique(covered)) == 250  # contiguous, no overlap
    for fold in folds:
        assert fold.train_idx.max() < fold.test_idx.min() - 5


def test_walkforward_anchor_start_is_legacy_behaviour():
    """anchor='start' reproduces the old forward march (and its blind spot)."""
    n = 1000
    X = pd.DataFrame({"a": np.arange(n)})
    wf = PurgedWalkForward(
        n_splits=5,
        test_size=50,
        min_train_size=200,
        purge_horizon=5,
        embargo=5,
        anchor="start",
    )
    folds = list(wf.split(X))
    covered = np.concatenate([f.test_idx for f in folds])
    assert covered.min() == 200
    # The whole reason A1 exists: the tail is never scored.
    assert covered.max() == 449
    assert covered.max() < n - 1


def test_walkforward_rejects_unknown_anchor():
    with pytest.raises(ValueError):
        PurgedWalkForward(anchor="middle")


def test_walkforward_anchor_end_truncates_when_history_is_short():
    n = 300
    X = pd.DataFrame({"a": np.arange(n)})
    wf = PurgedWalkForward(n_splits=6, test_size=50, min_train_size=200)
    folds = list(wf.split(X))
    assert len(folds) == 2  # only 300-100 >= 200 fits
    assert np.concatenate([f.test_idx for f in folds]).max() == n - 1


def test_regime_baselines_known_case():
    """Closed-form baseline battery on a hand-computable label mix (A3)."""
    y = np.array([0] * 30 + [1] * 18 + [2] * 12)
    base = regime_baselines(y)

    assert base["n_rows"] == 60.0
    assert base["majority"] == pytest.approx(0.5)
    assert base["marginal_random"] == pytest.approx(0.38)  # .5^2 + .3^2 + .2^2
    # A constant "A2" call is adjacent to every observed class on the 6-cycle.
    assert base["best_constant_adjacent"] == pytest.approx(1.0)
    assert base["best_constant_cycdist"] == pytest.approx(0.7)
    # (1 - sum p_k^2) / 6
    assert base["constant_prior_brier"] == pytest.approx(0.62 / 6)
    assert base["constant_prior_ece"] == pytest.approx(0.0, abs=1e-12)
    # Uniform probabilities: 5/36 whatever the label distribution.
    assert base["uniform_brier"] == pytest.approx(5.0 / 36.0)
    assert base["persistence"] == pytest.approx(57 / 59)


def test_regime_baselines_beat_the_current_regime_head_on_brier():
    """Guard the finding that motivated A3: a constant prior wins on Brier."""
    rng = np.random.default_rng(7)
    y = rng.integers(0, 6, size=600)
    base = regime_baselines(y)
    assert base["constant_prior_brier"] < 0.1623  # measured ensemble_direct Brier
    assert base["best_constant_adjacent"] >= 0.45  # structural floor, not skill


def test_quadratic_weighted_kappa_bounds():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 6, size=400)
    assert quadratic_weighted_kappa(y, y) == pytest.approx(1.0)
    # A constant predictor is exactly chance under the cyclic weighting.
    assert quadratic_weighted_kappa(y, np.zeros_like(y)) == pytest.approx(0.0, abs=1e-12)
    shuffled = rng.permutation(y)
    assert abs(quadratic_weighted_kappa(y, shuffled)) < 0.15


def test_transition_f1_punishes_an_iid_random_predictor():
    """A4: recall alone gives iid noise 1.000; the matched F1 must not."""
    idx = pd.bdate_range("2015-01-01", periods=1000)
    truth = pd.Series(np.repeat(np.arange(20) % 6, 50)[:1000], index=idx)
    noise = pd.Series(
        np.random.default_rng(3).integers(0, 6, size=1000), index=idx
    )
    ev = evaluate_transitions(truth, noise, window=10)

    assert ev.n_pred_transitions > 700  # flips almost every bar
    assert ev.hit_within_window > 0.9  # the old headline: noise looks perfect
    assert ev.precision_within_window < 0.1
    assert ev.f1_within_window < 0.2


def test_transition_f1_rewards_an_aligned_predictor():
    idx = pd.bdate_range("2015-01-01", periods=1000)
    truth = pd.Series(np.repeat(np.arange(20) % 6, 50)[:1000], index=idx)
    lagged = truth.shift(3).bfill().astype(int)
    ev = evaluate_transitions(truth, lagged, window=10)
    assert ev.f1_within_window > 0.9
    assert ev.matched_within_window == ev.n_true_transitions


def _market_row(symbol: str, **over) -> dict:
    row = {
        "symbol": symbol,
        "direction_hit": 0.60,
        "direction_ci_low": 0.52,
        "mae_ratio": 0.98,
        "interval_coverage": 0.80,
        "always_up_hit": 0.55,
        "baseline_hit": 0.50,
    }
    row.update(over)
    return row


def _regime_block(raw=0.17, temporal=0.18, candidate=0.20) -> dict:
    return {
        "gbm_unsmoothed": {"regime_macro_f1": raw},
        "gbm_temporal": {"regime_macro_f1": temporal},
        "ensemble_direct": {"regime_macro_f1": candidate},
    }


def test_gates_pass_only_with_real_skill():
    rows = [_market_row(s) for s in ("KS11", "^GSPC", "BTC-USD")]
    gates, inputs = build_gates(rows, _regime_block(), include_regime=True)
    assert all(gates.values()), [k for k, v in gates.items() if not v]
    assert inputs["regime_floor"] == pytest.approx(0.18)


def test_gate_direction_skill_vs_trivial_fails_a_biased_coin():
    """A5: 69.2% hit in a market that rose 76% of the time is not skill."""
    rows = [
        _market_row("KS11"),
        _market_row("^GSPC", direction_hit=0.692, always_up_hit=0.756),
        _market_row("BTC-USD"),
    ]
    gates, inputs = build_gates(rows, _regime_block(), include_regime=True)
    assert gates["direction_skill_vs_trivial"] is False
    assert inputs["direction_skill_vs_trivial"]["^GSPC"] < 0


def test_gate_ci_low_must_exclude_chance():
    rows = [_market_row(s) for s in ("KS11", "^GSPC", "BTC-USD")]
    rows[1]["direction_ci_low"] = 0.41
    gates, _ = build_gates(rows, _regime_block(), include_regime=True)
    assert gates["direction_ci_low_excludes_chance"] is False


def test_gate_skipping_regime_is_a_hard_fail():
    rows = [_market_row(s) for s in ("KS11", "^GSPC", "BTC-USD")]
    gates, _ = build_gates(rows, {}, include_regime=False)
    assert gates["regime_evaluated"] is False
    assert gates["regime_macro_f1_non_regression"] is False


def test_gate_missing_regime_score_is_not_a_zero_floor():
    rows = [_market_row(s) for s in ("KS11", "^GSPC", "BTC-USD")]
    regime = _regime_block()
    regime.pop("ensemble_direct")
    gates, inputs = build_gates(rows, regime, include_regime=True)
    assert gates["regime_macro_f1_non_regression"] is False
    assert inputs["regime_candidate_f1"] is None


def test_gate_regime_non_regression_has_no_slack():
    rows = [_market_row(s) for s in ("KS11", "^GSPC", "BTC-USD")]
    # 0.1719 vs a 0.1740 temporal floor passed only because of the old * 0.98.
    gates, _ = build_gates(
        rows, _regime_block(raw=0.1731, temporal=0.1740, candidate=0.1719), include_regime=True
    )
    assert gates["regime_macro_f1_non_regression"] is False


def test_bootstrap_block_follows_the_origin_stride():
    assert bootstrap_block_for_stride(21) == 9
    assert bootstrap_block_for_stride(42) == 6
    assert bootstrap_block_for_stride(63) == 3
    assert bootstrap_block_for_stride(126) == 3
    assert GATE_THRESHOLDS["trivial_slack"] == pytest.approx(0.01)


def test_leakage_auditor_blocks_gold_in_train(synth):
    market, _ = synth
    feats = build_features(market.ohlcv, market.extras)
    X = model_matrix(feats).dropna()
    y = weak_labels(feats).reindex(X.index)
    gold = gold_labels(market.ohlcv["close"]).reindex(X.index)
    report = LeakageAuditor().audit(
        X, y, gold_labels=gold, gold_used_in_training=True, execution_lag=1
    )
    assert not report.passed
    assert any(f.code == "GOLD_IN_TRAIN" for f in report.findings)


def test_leakage_auditor_blocks_zero_lag(synth):
    market, _ = synth
    feats = build_features(market.ohlcv, market.extras)
    X = model_matrix(feats).dropna()
    y = weak_labels(feats).reindex(X.index)
    report = LeakageAuditor().audit(X, y, execution_lag=0)
    assert not report.passed


def test_backtest_execution_lag_changes_result(synth):
    market, planted = synth
    prices = market.ohlcv["close"]
    a = economic_backtest(prices, planted, BacktestConfig(execution_lag=1, cost_bps=0))
    b = economic_backtest(prices, planted, BacktestConfig(execution_lag=5, cost_bps=0))
    assert not np.isclose(a.total_return, b.total_return) or a.turnover != b.turnover


def test_weak_labels_not_identical_to_gold(synth):
    market, _ = synth
    feats = build_features(market.ohlcv, market.extras)
    weak = weak_labels(feats)
    gold = gold_labels(market.ohlcv["close"])
    agree = (weak == gold.reindex(weak.index)).mean()
    assert agree < 0.98


def test_hmm_fit_predict_shapes(synth):
    market, _ = synth
    X, y_weak, _, _ = prepare_xy(market)
    valid = X.dropna().index.intersection(y_weak.dropna().index)
    X, y_weak = X.loc[valid], y_weak.loc[valid]
    mid = len(X) // 2
    model = KostolanyHMM(n_states=6, n_iter=50)
    pred, proba = model.fit_predict(X.iloc[:mid], y_weak.iloc[:mid], X.iloc[mid:])
    assert len(pred.dropna()) > 0
    assert proba.shape[1] == 6
    assert np.allclose(proba.dropna().sum(axis=1), 1.0, atol=1e-5)


def test_experiment_runner_oos_and_artifacts(tmp_path):
    market, planted = make_synthetic(n=900, seed=1)
    X, y_weak, y_gold, prices = prepare_xy(market)
    runner = ExperimentRunner(
        ExperimentConfig(
            name="test_wf",
            cv="walkforward",
            n_splits=2,
            purge_horizon=3,
            embargo=2,
            output_dir=str(tmp_path),
        )
    )

    def fp(Xtr, ytr, Xte):
        return KostolanyHMM(n_states=6, n_iter=40).fit_predict(Xtr, ytr, Xte)

    result = runner.run(X, y_weak, prices, fp, y_gold=planted.reindex(X.index))
    assert result.passed_leakage
    assert result.oos_predictions is not None
    assert result.oos_predictions.notna().sum() > 50
    assert list(tmp_path.glob("test_wf_*.json"))


def test_evaluate_regimes_smoke():
    idx = pd.bdate_range("2020-01-01", periods=100)
    y = pd.Series(np.random.default_rng(0).integers(0, 6, size=100), index=idx)
    p = y.copy()
    p.iloc[10:20] = (p.iloc[10:20] + 1) % 6
    proba = pd.DataFrame(
        np.eye(6)[p.to_numpy()],
        index=idx,
        columns=[f"p{i}" for i in range(6)],
    )
    report = evaluate_regimes(y, p, proba)
    assert 0 <= report.accuracy <= 1
    assert report.macro_f1 >= 0
    # Baseline battery + QWK travel with every report (A3).
    assert report.baselines["n_rows"] == 100.0
    assert 0.0 <= report.baselines["majority"] <= 1.0
    assert -1.0 <= report.quadratic_weighted_kappa <= 1.0
    d = report.to_dict()
    assert "baselines" in d and "quadratic_weighted_kappa" in d
    assert "f1_within_window" in d["transition"]
