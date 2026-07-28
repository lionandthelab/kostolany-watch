"""Leakage-safe pooled cross-asset 63-day forecast (research + production head).

Production serving lives in ``kostolany.pooled_serve`` (equity Flows / 파도꾼).
This module owns the model, panel loaders, and walk-forward evaluator.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any, Iterable

import numpy as np
import pandas as pd

from kostolany.connectors import load_market
from kostolany.engine import prepare_xy
from kostolany.harness.commercial_eval import (
    HORIZON,
    MarketScore,
    _block_bootstrap_hit_ci,
    _safe_corr,
)

DEFAULT_PANEL = (
    "KS11",
    "^GSPC",
    "BTC-USD",
    "SPY",
    "EEM",
    "EWJ",
    "FXI",
    "GLD",
    "USO",
    "TLT",
    "XLE",
    "XLK",
)


def _asset_group(symbol: str) -> tuple[float, float, float, float]:
    symbol = symbol.upper()
    if "BTC" in symbol:
        return (0.0, 0.0, 0.0, 1.0)
    if symbol in {"GLD", "USO", "SLV", "PDBC"}:
        return (0.0, 1.0, 0.0, 0.0)
    if symbol in {"TLT", "IEF", "HYG"}:
        return (0.0, 0.0, 1.0, 0.0)
    return (1.0, 0.0, 0.0, 0.0)


def _tabular_design(X: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Causal multi-scale tabular representation for pooled trees."""
    core = X.copy()
    frames = [core.add_suffix("_t0")]
    lag_cols = [
        c
        for c in (
            "log_ret_1",
            "ret_5",
            "ret_20",
            "ret_60",
            "rv_10",
            "rv_60",
            "vol_z",
            "breadth_proxy",
            "trend_slope",
            "trend_accel",
            "drawdown",
            "money_proxy",
            "sentiment_proxy",
        )
        if c in core.columns
    ]
    for lag in (5, 20, 60):
        frames.append(core[lag_cols].shift(lag).add_suffix(f"_lag{lag}"))

    log_ret = core["log_ret_1"]
    summary = pd.DataFrame(index=core.index)
    for window in (5, 20, 60, 252):
        summary[f"log_mean_{window}"] = log_ret.rolling(
            window, min_periods=max(3, window // 3)
        ).mean()
        summary[f"log_std_{window}"] = log_ret.rolling(
            window, min_periods=max(3, window // 3)
        ).std()
        summary[f"log_sum_{window}"] = log_ret.rolling(
            window, min_periods=max(3, window // 3)
        ).sum()
    equity, commodity, bond, crypto = _asset_group(symbol)
    summary["group_equity"] = equity
    summary["group_commodity"] = commodity
    summary["group_bond"] = bond
    summary["group_crypto"] = crypto
    return pd.concat([*frames, summary], axis=1).replace(
        [np.inf, -np.inf], np.nan
    )


def load_panel(
    symbols: Iterable[str] = DEFAULT_PANEL,
    *,
    start: str = "2010-01-01",
) -> dict[str, tuple[pd.DataFrame, pd.Series]]:
    panel: dict[str, tuple[pd.DataFrame, pd.Series]] = {}
    for symbol in symbols:
        market = load_market(symbol, start=start, enrich_fred=False)
        X, y_weak, _y_gold, prices = prepare_xy(market)
        valid = (
            X.dropna()
            .index.intersection(y_weak.dropna().index)
            .intersection(prices.dropna().index)
        )
        X = X.loc[valid]
        panel[symbol] = (_tabular_design(X, symbol), prices.loc[valid].astype(float))
    return panel


class PooledDirectModel:
    """Recency-weighted global LightGBM heads trained across asset classes."""

    def __init__(self, *, n_estimators: int = 220, random_state: int = 42) -> None:
        self.n_estimators = n_estimators
        self.random_state = random_state
        self.median_: Any | None = None
        self.low_: Any | None = None
        self.high_: Any | None = None
        self.direction_: Any | None = None
        self.columns_: list[str] = []
        self.group_radius_: dict[str, float] = {}

    def _regressor(self, objective: str, alpha: float | None = None):
        import lightgbm as lgb

        kwargs: dict[str, Any] = {
            "objective": objective,
            "n_estimators": self.n_estimators,
            "learning_rate": 0.035,
            "num_leaves": 31,
            "max_depth": 7,
            "min_child_samples": 70,
            "subsample": 0.8,
            "subsample_freq": 1,
            "colsample_bytree": 0.75,
            "reg_alpha": 0.3,
            "reg_lambda": 5.0,
            "random_state": self.random_state,
            "n_jobs": 1,
            "verbosity": -1,
        }
        if alpha is not None:
            kwargs["alpha"] = alpha
        return lgb.LGBMRegressor(**kwargs)

    def fit(
        self,
        panel: dict[str, tuple[pd.DataFrame, pd.Series]],
        cutoff: pd.Timestamp,
    ) -> "PooledDirectModel":
        rows: list[pd.DataFrame] = []
        targets: list[pd.Series] = []
        weights: list[pd.Series] = []
        group_targets: dict[str, list[float]] = {}
        for symbol, (design_full, _prices) in panel.items():
            design_slice = design_full.loc[:cutoff]
            design = design_slice.dropna()
            if len(design) < 400:
                continue
            # Construct the 63-bar target before feature-row filtering so an
            # interior feature NaN cannot silently stretch the horizon.
            log_ret = design_slice["log_ret_1_t0"]
            target = log_ret.rolling(HORIZON, min_periods=HORIZON).sum().shift(
                -HORIZON
            )
            valid = design.index.intersection(target.dropna().index)
            if len(valid) < 300:
                continue
            # Balance assets, then favor recent regimes with a five-year half-life.
            age_days = (cutoff - pd.DatetimeIndex(valid)).days.to_numpy()
            recency = np.exp(-np.log(2.0) * age_days / (365.25 * 5.0))
            weight = recency * (1000.0 / max(1, len(valid)))
            rows.append(design.loc[valid])
            targets.append(target.loc[valid])
            weights.append(pd.Series(weight, index=valid, name=symbol))
            group = (
                "crypto"
                if _asset_group(symbol)[3]
                else "commodity"
                if _asset_group(symbol)[1]
                else "bond"
                if _asset_group(symbol)[2]
                else "equity"
            )
            group_targets.setdefault(group, []).extend(
                target.loc[valid].astype(float).tolist()
            )
        if not rows:
            raise ValueError(f"No pooled rows before {cutoff.date()}")

        train_x = pd.concat(rows, axis=0, ignore_index=True)
        train_y = pd.concat(targets, axis=0, ignore_index=True).astype(float)
        train_w = pd.concat(weights, axis=0, ignore_index=True).astype(float)
        self.columns_ = list(train_x.columns)
        values = train_y.to_numpy()
        lo, hi = np.quantile(values, [0.0025, 0.9975])
        values = np.clip(values, lo, hi)

        self.median_ = self._regressor("huber")
        self.low_ = self._regressor("quantile", 0.10)
        self.high_ = self._regressor("quantile", 0.90)
        self.median_.fit(train_x, values, sample_weight=train_w)
        self.low_.fit(train_x, values, sample_weight=train_w)
        self.high_.fit(train_x, values, sample_weight=train_w)

        import lightgbm as lgb

        self.direction_ = lgb.LGBMClassifier(
            objective="binary",
            n_estimators=self.n_estimators,
            learning_rate=0.035,
            num_leaves=31,
            max_depth=7,
            min_child_samples=70,
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.75,
            reg_alpha=0.3,
            reg_lambda=5.0,
            class_weight="balanced",
            random_state=self.random_state,
            n_jobs=1,
            verbosity=-1,
        )
        self.direction_.fit(train_x, (train_y > 0).astype(int), sample_weight=train_w)
        self.group_radius_ = {
            group: float(np.quantile(np.abs(values), 0.80))
            for group, values in group_targets.items()
            if values
        }
        return self

    def predict(self, row: pd.DataFrame) -> tuple[float, float, float, float]:
        if any(model is None for model in (self.median_, self.low_, self.high_, self.direction_)):
            raise RuntimeError("Pooled model not fitted")
        x = row.reindex(columns=self.columns_).fillna(0.0)
        median_log = float(self.median_.predict(x)[0])
        low_log = float(self.low_.predict(x)[0])
        high_log = float(self.high_.predict(x)[0])
        p_up = float(self.direction_.predict_proba(x)[0, 1])
        # Separate direction and magnitude heads; pooled classifier has enough
        # cross-asset samples to estimate sign without a tiny per-asset split.
        sign = 1.0 if p_up >= 0.5 else -1.0
        median_log = sign * 0.70 * abs(median_log)
        low_log, high_log = sorted((low_log, high_log))
        radius = max(abs(median_log - low_log), abs(high_log - median_log))
        group = (
            "crypto"
            if float(x.get("group_crypto", pd.Series([0.0])).iloc[0]) > 0.5
            else "commodity"
            if float(x.get("group_commodity", pd.Series([0.0])).iloc[0]) > 0.5
            else "bond"
            if float(x.get("group_bond", pd.Series([0.0])).iloc[0]) > 0.5
            else "equity"
        )
        radius = max(radius, self.group_radius_.get(group, 0.0))
        low_log, high_log = median_log - radius, median_log + radius
        return (
            float(np.expm1(np.clip(median_log, -1.2, 1.5))),
            float(np.expm1(np.clip(low_log, -1.2, 1.5))),
            float(np.expm1(np.clip(high_log, -1.2, 1.5))),
            p_up,
        )


def evaluate_pooled_forecast(
    targets: Iterable[str] = ("KS11", "^GSPC", "BTC-USD"),
    *,
    panel_symbols: Iterable[str] = DEFAULT_PANEL,
    start: str = "2010-01-01",
    origin_stride: int = 63,
    refit_days: int = 730,
) -> dict[str, Any]:
    started = time.perf_counter()
    panel = load_panel(panel_symbols, start=start)
    target_symbols = [symbol for symbol in targets if symbol in panel]
    records: list[tuple[pd.Timestamp, str, int]] = []
    for symbol in target_symbols:
        design, prices = panel[symbol]
        n = min(len(design), len(prices))
        for origin in range(756, n - HORIZON, origin_stride):
            records.append((pd.Timestamp(design.index[origin - 1]), symbol, origin))
    records.sort()

    predictions: dict[str, list[tuple[float, float, float, float, float, float]]] = {
        symbol: [] for symbol in target_symbols
    }
    model: PooledDirectModel | None = None
    last_fit: pd.Timestamp | None = None
    for date, symbol, origin in records:
        if model is None or last_fit is None or (date - last_fit).days >= refit_days:
            model = PooledDirectModel().fit(panel, date)
            last_fit = date
        design, prices = panel[symbol]
        median, low, high, p_up = model.predict(design.iloc[[origin - 1]])
        realized = float(prices.iloc[origin - 1 + HORIZON] / prices.iloc[origin - 1] - 1.0)
        log_hist = np.log(prices.iloc[max(0, origin - 253) : origin]).diff().dropna()
        baseline = float(np.expm1(log_hist.mean() * HORIZON))
        predictions[symbol].append((median, low, high, p_up, realized, baseline))

    scores: dict[str, Any] = {}
    for symbol, values in predictions.items():
        arr = np.asarray(values, dtype=float)
        pred, low, high, p_up, real, baseline = arr.T
        hits = (np.sign(pred) == np.sign(real)).astype(float)
        ci_low, ci_high = _block_bootstrap_hit_ci(hits, block=1)
        mae = float(np.mean(np.abs(pred - real)))
        base_mae = float(np.mean(np.abs(baseline - real)))
        always_up = float(np.mean(real > 0))
        score = MarketScore(
            symbol=symbol,
            n_origins=len(real),
            direction_hit=float(np.mean(hits)),
            direction_ci_low=ci_low,
            direction_ci_high=ci_high,
            mae=mae,
            rmse=float(np.sqrt(np.mean((pred - real) ** 2))),
            correlation=_safe_corr(pred, real),
            brier_up=float(np.mean((p_up - (real > 0).astype(float)) ** 2)),
            interval_coverage=float(np.mean((real >= low) & (real <= high))),
            interval_width=float(np.mean(high - low)),
            baseline_hit=float(np.mean(np.sign(baseline) == np.sign(real))),
            always_up_hit=always_up,
            predicted_up_rate=float(np.mean(pred > 0)),
            skill_vs_always_up=float(np.mean(hits) - always_up),
            mean_magnitude_scale=0.70,
            baseline_mae=base_mae,
            mae_ratio=mae / max(1e-12, base_mae),
            elapsed_seconds=0.0,
        )
        scores[symbol] = asdict(score)
    return {
        "version": "pooled-v1",
        "elapsed_seconds": time.perf_counter() - started,
        "panel": list(panel),
        "markets": scores,
    }
