"""Commercial-v4 leakage-safe multi-market evaluator.

The name describes a promotion bar, not an investment-performance claim.
Gold regime labels are used only by ``evaluate_regime_model`` for scoring.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from scipy.stats import norm

from kostolany.connectors import load_market
from kostolany.engine import prepare_xy
from kostolany.harness.forecast_tune import evaluate_regime_model
from kostolany.models import KostolanyGBM
from kostolany.tsfm import LocalTSFM, TSFMEnsemble

HORIZON = 63


@dataclass
class MarketScore:
    symbol: str
    n_origins: int
    direction_hit: float
    direction_ci_low: float
    direction_ci_high: float
    mae: float
    rmse: float
    correlation: float
    brier_up: float
    interval_coverage: float
    interval_width: float
    baseline_hit: float
    always_up_hit: float
    predicted_up_rate: float
    skill_vs_always_up: float
    mean_magnitude_scale: float
    baseline_mae: float
    mae_ratio: float
    elapsed_seconds: float


def _block_bootstrap_hit_ci(
    hit: np.ndarray,
    *,
    block: int = 3,
    n_boot: int = 1200,
    seed: int = 42,
) -> tuple[float, float]:
    """Moving-block bootstrap CI for overlapping 63d/21d origins."""
    if len(hit) < 8:
        return 0.0, 1.0
    rng = np.random.default_rng(seed)
    starts = np.arange(max(1, len(hit) - block + 1))
    values = np.empty(n_boot, dtype=float)
    blocks_needed = int(np.ceil(len(hit) / block))
    for i in range(n_boot):
        chosen = rng.choice(starts, size=blocks_needed, replace=True)
        sample = np.concatenate([hit[s : s + block] for s in chosen])[: len(hit)]
        values[i] = float(np.mean(sample))
    lo, hi = np.quantile(values, [0.025, 0.975])
    return float(lo), float(hi)


def _safe_corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3 or np.std(a) < 1e-12 or np.std(b) < 1e-12:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def evaluate_flow_market(
    symbol: str,
    *,
    start: str = "2010-01-01",
    origin_stride: int = 21,
    refit_stride: int = 126,
    min_train: int = 756,
    model_kwargs: dict[str, Any] | None = None,
) -> MarketScore:
    """Expanding-origin direct 63d forecast evaluation.

    At origin ``t``, LocalTSFM receives only ``X[:t]``. Its internal h63
    labels end at or before ``t-1``; rows whose target would cross the origin
    are NaN and excluded.
    """
    started = time.perf_counter()
    market = load_market(symbol, start=start, enrich_fred=True)
    X, y_weak, _y_gold, prices = prepare_xy(market)
    valid = (
        X.dropna()
        .index.intersection(y_weak.dropna().index)
        .intersection(prices.dropna().index)
    )
    X = X.loc[valid]
    prices = prices.loc[valid].astype(float)

    if len(X) < min_train + HORIZON + 5:
        raise ValueError(f"{symbol}: only {len(X)} clean rows")

    predicted: list[float] = []
    actual: list[float] = []
    q_low: list[float] = []
    q_high: list[float] = []
    p_up: list[float] = []
    baseline: list[float] = []
    magnitude_scales: list[float] = []
    origins = range(min_train, len(X) - HORIZON, origin_stride)
    model: LocalTSFM | None = None
    last_fit = -100_000

    for origin in origins:
        if model is None or origin - last_fit >= refit_stride:
            model = LocalTSFM(**(model_kwargs or {}))
            model.fit(X.iloc[:origin])
            last_fit = origin

        tail = X.iloc[max(0, origin - 90) : origin]
        traj = model.predict_trajectory(tail)
        median = float(traj.ret_hat["h63"].iloc[-1])
        if traj.quantiles is not None:
            lo = float(traj.quantiles["h63_q10"].iloc[-1])
            hi = float(traj.quantiles["h63_q90"].iloc[-1])
        else:
            lo = hi = median
        lo, hi = min(lo, hi, median), max(lo, hi, median)

        px0 = float(prices.iloc[origin - 1])
        px1 = float(prices.iloc[origin - 1 + HORIZON])
        realized = px1 / px0 - 1.0

        # Causal trailing-drift baseline.
        log_hist = np.log(prices.iloc[max(0, origin - 253) : origin]).diff().dropna()
        base = float(np.expm1(log_hist.mean() * HORIZON))

        if traj.direction_proba is not None:
            prob_up = float(traj.direction_proba.iloc[-1])
        else:
            # Convert q10/q90 width into a conservative normal approximation.
            sigma = max(1e-4, (hi - lo) / (2.0 * 1.2815515655446004))
            prob_up = float(norm.cdf(median / sigma))

        predicted.append(median)
        actual.append(realized)
        q_low.append(lo)
        q_high.append(hi)
        p_up.append(prob_up)
        baseline.append(base)
        magnitude_scales.append(float(model.long_magnitude_scale_))

    p = np.asarray(predicted, dtype=float)
    r = np.asarray(actual, dtype=float)
    lo = np.asarray(q_low, dtype=float)
    hi = np.asarray(q_high, dtype=float)
    prob = np.asarray(p_up, dtype=float)
    base = np.asarray(baseline, dtype=float)
    hits = (np.sign(p) == np.sign(r)).astype(float)
    base_hits = (np.sign(base) == np.sign(r)).astype(float)
    always_up = float(np.mean(r > 0))
    ci_low, ci_high = _block_bootstrap_hit_ci(hits)
    mae = float(np.mean(np.abs(p - r)))
    baseline_mae = float(np.mean(np.abs(base - r)))

    return MarketScore(
        symbol=symbol,
        n_origins=len(p),
        direction_hit=float(np.mean(hits)),
        direction_ci_low=ci_low,
        direction_ci_high=ci_high,
        mae=mae,
        rmse=float(np.sqrt(np.mean((p - r) ** 2))),
        correlation=_safe_corr(p, r),
        brier_up=float(np.mean((prob - (r > 0).astype(float)) ** 2)),
        interval_coverage=float(np.mean((r >= lo) & (r <= hi))),
        interval_width=float(np.mean(hi - lo)),
        baseline_hit=float(np.mean(base_hits)),
        always_up_hit=always_up,
        predicted_up_rate=float(np.mean(p > 0)),
        skill_vs_always_up=float(np.mean(hits) - always_up),
        mean_magnitude_scale=float(np.mean(magnitude_scales)),
        baseline_mae=baseline_mae,
        mae_ratio=mae / max(1e-12, baseline_mae),
        elapsed_seconds=time.perf_counter() - started,
    )


def evaluate_regime_candidates(
    symbol: str = "KS11",
    *,
    start: str = "2010-01-01",
    n_splits: int = 6,
) -> dict[str, Any]:
    """Compare raw GBM, temporal GBM, and direct-trajectory ensemble."""
    market = load_market(symbol, start=start, enrich_fred=True)
    X, y_weak, y_gold, prices = prepare_xy(market)
    valid = (
        X.dropna()
        .index.intersection(y_weak.dropna().index)
        .intersection(prices.dropna().index)
    )
    X = X.loc[valid]
    y_weak = y_weak.loc[valid].astype(int)
    y_gold = y_gold.reindex(valid)
    prices = prices.loc[valid]

    configs = {
        "gbm_unsmoothed": lambda: KostolanyGBM(
            calibrate=False, cycle_smooth=0.0
        ),
        "gbm_temporal": lambda: KostolanyGBM(
            calibrate=True, cycle_smooth=0.22
        ),
        "ensemble_direct": lambda: TSFMEnsemble(
            w_hmm=0.25,
            w_gbm=0.55,
            w_tsfm=0.20,
            transition_soften=0.10,
            gbm_kwargs={
                "n_estimators": 500,
                "learning_rate": 0.028,
                "num_leaves": 55,
                "cycle_smooth": 0.0,
            },
        ),
    }
    scores: dict[str, Any] = {}
    for name, factory in configs.items():
        score = evaluate_regime_model(
            X,
            y_weak,
            y_gold,
            prices,
            factory,
            n_splits=n_splits,
            name=name,
        )
        scores[name] = score.to_dict()
    return scores


def run_commercial_evaluation(
    symbols: Iterable[str],
    *,
    start: str = "2010-01-01",
    output_dir: str | Path = "artifacts/experiments",
    include_regime: bool = True,
    origin_stride: int = 21,
    refit_stride: int = 126,
) -> dict[str, Any]:
    started = time.perf_counter()
    markets: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for symbol in symbols:
        symbol = symbol.strip()
        if not symbol:
            continue
        try:
            markets[symbol] = asdict(
                evaluate_flow_market(
                    symbol,
                    start=start,
                    origin_stride=origin_stride,
                    refit_stride=refit_stride,
                )
            )
        except Exception as exc:  # noqa: BLE001
            errors[symbol] = str(exc)

    regime = (
        evaluate_regime_candidates("KS11", start=start) if include_regime else {}
    )
    market_rows = list(markets.values())
    hits = [float(row["direction_hit"]) for row in market_rows]
    ratios = [float(row["mae_ratio"]) for row in market_rows]
    coverages = [float(row["interval_coverage"]) for row in market_rows]
    direction_skill = [float(row["skill_vs_always_up"]) for row in market_rows]
    raw_f1 = float(
        (regime.get("gbm_unsmoothed") or {}).get("regime_macro_f1") or 0.0
    )
    candidate_f1 = float(
        (regime.get("ensemble_direct") or {}).get("regime_macro_f1") or 0.0
    )
    temporal_f1 = float(
        (regime.get("gbm_temporal") or {}).get("regime_macro_f1") or 0.0
    )
    # Prefer non-regression vs temporally calibrated GBM (fairer than raw).
    regime_floor = max(raw_f1 * 0.98, temporal_f1 * 0.98)

    gates = {
        "all_markets_completed": len(market_rows) >= 3 and not errors,
        "median_direction_hit": bool(hits and np.median(hits) >= 0.52),
        "market_floor": bool(hits and min(hits) >= 0.48),
        "direction_skill_vs_baseline": bool(
            market_rows
            and sum(
                float(row["direction_hit"]) >= float(row["baseline_hit"]) - 0.01
                for row in market_rows
            )
            >= 2
        ),
        # Always-up is reported but not a hard gate: long equity bull windows
        # push the bar above 0.75, which is not an actionable product KPI.
        "median_mae_ratio": bool(ratios and np.median(ratios) <= 1.02),
        "interval_coverage": bool(
            coverages
            and all(0.65 <= coverage <= 0.95 for coverage in coverages)
        ),
        "regime_macro_f1_non_regression": bool(
            not include_regime or candidate_f1 >= regime_floor
        ),
    }
    passed = all(gates.values())
    score = 0.0
    if hits and ratios:
        score = float(
            100.0
            * (
                0.55 * np.median(hits)
                + 0.25 * np.clip(1.0 - np.median(ratios), -1.0, 1.0)
                + 0.20 * candidate_f1
            )
        )

    result: dict[str, Any] = {
        "pass": passed,
        "score": score,
        "version": "commercial-v5",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": time.perf_counter() - started,
        "markets": markets,
        "regime": regime,
        "gates": gates,
        "errors": errors,
        "constraints": {
            "gold_used_for_training": False,
            "target_horizon": HORIZON,
            "forecast_origin": "close_t",
            "forecast_target": "close_t+63 / close_t - 1",
            "economic_backtest": False,
            "origin_stride": origin_stride,
            "refit_stride": refit_stride,
        },
    }
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = output / f"commercial_v5_{stamp}.json"
    artifact.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["artifact"] = str(artifact)
    return result
