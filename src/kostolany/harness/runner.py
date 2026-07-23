"""Walk-forward experiment runner that wires model + harness together."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from kostolany.harness.backtest import BacktestConfig, economic_backtest
from kostolany.harness.cv import CombinatorialPurgedCV, PurgedWalkForward
from kostolany.harness.leakage import LeakageAuditor
from kostolany.harness.metrics import evaluate_regimes


@dataclass
class ExperimentConfig:
    name: str = "walkforward_hmm"
    cv: str = "walkforward"  # "walkforward" | "cpcv"
    n_splits: int = 4
    purge_horizon: int = 5
    embargo: int = 5
    execution_lag: int = 1
    cost_bps: float = 5.0
    output_dir: str = "artifacts/experiments"


@dataclass
class ExperimentResult:
    name: str
    timestamp: str
    leakage: dict
    metrics: dict
    backtest: dict
    fold_metrics: list[dict] = field(default_factory=list)
    oos_predictions: pd.Series | None = None
    oos_proba: pd.DataFrame | None = None
    passed_leakage: bool = False

    def save(self, directory: str | Path) -> Path:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        stamp = self.timestamp.replace(":", "").replace("+00:00", "Z")
        path = root / f"{self.name}_{stamp}.json"
        payload = {
            "name": self.name,
            "timestamp": self.timestamp,
            "leakage": self.leakage,
            "metrics": self.metrics,
            "backtest": self.backtest,
            "fold_metrics": self.fold_metrics,
            "passed_leakage": self.passed_leakage,
        }
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        if self.oos_predictions is not None:
            self.oos_predictions.to_csv(
                root / f"{self.name}_{stamp}_preds.csv", header=["regime"]
            )
        if self.oos_proba is not None:
            self.oos_proba.to_csv(root / f"{self.name}_{stamp}_proba.csv")
        return path


FitPredictFn = Callable[
    [pd.DataFrame, pd.Series, pd.DataFrame],
    tuple[pd.Series, pd.DataFrame],
]
# (X_train, y_train, X_test) -> (y_pred, proba_df)


class ExperimentRunner:
    def __init__(self, config: ExperimentConfig | None = None) -> None:
        self.config = config or ExperimentConfig()
        self.auditor = LeakageAuditor()

    def run(
        self,
        X: pd.DataFrame,
        y_weak: pd.Series,
        prices: pd.Series,
        fit_predict: FitPredictFn,
        *,
        y_gold: pd.Series | None = None,
        returns: pd.Series | None = None,
        gold_used_in_training: bool = False,
    ) -> ExperimentResult:
        cfg = self.config
        X = X.sort_index()
        y_weak = y_weak.reindex(X.index)
        prices = prices.reindex(X.index)

        # Drop warm-up NaNs collectively
        valid = X.dropna().index.intersection(y_weak.dropna().index)
        X = X.loc[valid]
        y_weak = y_weak.loc[valid].astype(int)
        prices = prices.loc[valid]

        leak = self.auditor.audit(
            X,
            y_weak,
            gold_labels=y_gold.reindex(X.index) if y_gold is not None else None,
            gold_used_in_training=gold_used_in_training,
            returns=(
                returns.reindex(X.index) if returns is not None else prices.pct_change()
            ),
            execution_lag=cfg.execution_lag,
        )

        if cfg.cv == "cpcv":
            splitter = CombinatorialPurgedCV(
                n_groups=max(4, cfg.n_splits + 1),
                n_test_groups=2,
                purge_horizon=cfg.purge_horizon,
                embargo_pct=max(cfg.embargo / max(len(X), 1), 0.01),
            )
        else:
            splitter = PurgedWalkForward(
                n_splits=cfg.n_splits,
                purge_horizon=cfg.purge_horizon,
                embargo=cfg.embargo,
                expanding=True,
            )

        oos_pred = pd.Series(index=X.index, dtype=float)
        oos_proba = pd.DataFrame(
            index=X.index, columns=[f"p{i}" for i in range(6)], dtype=float
        )
        fold_metrics: list[dict] = []

        for fold in splitter.split(X):
            tr, te = fold.train_idx, fold.test_idx
            Xtr, ytr = X.iloc[tr], y_weak.iloc[tr]
            Xte = X.iloc[te]
            # Guardrail: never pass gold into fit_predict
            pred, proba = fit_predict(Xtr, ytr, Xte)
            oos_pred.iloc[te] = pred.reindex(Xte.index).to_numpy()
            for c in oos_proba.columns:
                if c in proba.columns:
                    oos_proba.loc[Xte.index, c] = proba[c].to_numpy()

            eval_y = (
                y_gold.reindex(Xte.index) if y_gold is not None else y_weak.iloc[te]
            )
            # Gold for scoring only
            report = evaluate_regimes(eval_y, pred.reindex(Xte.index), proba)
            fold_metrics.append(
                {
                    "fold_id": fold.fold_id,
                    "n_train": int(len(tr)),
                    "n_test": int(len(te)),
                    "purged": fold.purged_count,
                    "embargo": fold.embargo_count,
                    "macro_f1": report.macro_f1,
                    "accuracy": report.accuracy,
                    "ece": report.calibration.ece,
                }
            )

        scored = oos_pred.dropna()
        eval_full = (
            y_gold.reindex(scored.index)
            if y_gold is not None
            else y_weak.reindex(scored.index)
        )
        overall = evaluate_regimes(
            eval_full, scored, oos_proba.reindex(scored.index).dropna(how="all")
        )
        bt = economic_backtest(
            prices.reindex(scored.index),
            scored.astype(int),
            BacktestConfig(execution_lag=cfg.execution_lag, cost_bps=cfg.cost_bps),
            n_trials=max(1, len(fold_metrics)),
        )

        result = ExperimentResult(
            name=cfg.name,
            timestamp=datetime.now(timezone.utc).isoformat(),
            leakage=leak.to_dict(),
            metrics=overall.to_dict(),
            backtest=bt.to_dict(),
            fold_metrics=fold_metrics,
            oos_predictions=scored.astype(int),
            oos_proba=oos_proba.reindex(scored.index),
            passed_leakage=leak.passed,
        )
        result.save(cfg.output_dir)
        return result
