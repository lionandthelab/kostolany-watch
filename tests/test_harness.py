"""Harness unit tests — leakage, CV integrity, metrics, backtest lag."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from kostolany.data import make_synthetic
from kostolany.engine import prepare_xy
from kostolany.features import build_features, model_matrix
from kostolany.harness.backtest import BacktestConfig, economic_backtest
from kostolany.harness.cv import CombinatorialPurgedCV, PurgedWalkForward
from kostolany.harness.leakage import LeakageAuditor
from kostolany.harness.metrics import evaluate_regimes
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
