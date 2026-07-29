"""Commercial-v4 leakage-safe multi-market evaluator.

The name describes a promotion bar, not an investment-performance claim.
Gold regime labels are used only by ``evaluate_regime_model`` for scoring.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
from scipy.stats import norm

from kostolany.connectors import load_market
from kostolany.engine import prepare_xy
from kostolany.harness.forecast_tune import evaluate_regime_model
from kostolany.models import KostolanyGBM
from kostolany.tsfm import LocalTSFM, TSFMEnsemble

HORIZON = 63

# Bump whenever a threshold or a gate key changes so an old artifact can never
# be compared against a new bar without noticing.
GATE_SPEC_VERSION = "commercial-gate-v6"

GATE_THRESHOLDS: dict[str, float] = {
    "min_markets": 3.0,
    "median_direction_hit": 0.52,
    "market_floor": 0.48,
    # Beating the trailing-drift baseline in >= this many markets.
    "baseline_slack": 0.01,
    "baseline_min_markets": 2.0,
    # A binary sign task's trivial predictor is the better constant call,
    # i.e. max(always_up, 1 - always_up). Slack covers bootstrap jitter only.
    "trivial_slack": 0.01,
    # Block-bootstrap lower bound must at least exclude a coin flip.
    "direction_ci_low": 0.50,
    "median_mae_ratio": 1.02,
    "interval_coverage_min": 0.65,
    "interval_coverage_max": 0.95,
}


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


def bootstrap_block_for_stride(origin_stride: int) -> int:
    """Block length that spans the overlap induced by ``origin_stride``.

    Consecutive origins share ``HORIZON - origin_stride`` days of target, so a
    fixed ``block=3`` silently under-blocks whenever the stride shrinks. Three
    overlap lengths is the usual moving-block rule of thumb.
    """
    stride = max(1, int(origin_stride))
    return int(max(3, ceil(HORIZON / stride) * 3))


def _block_bootstrap_hit_ci(
    hit: np.ndarray,
    *,
    origin_stride: int | None = None,
    block: int | None = None,
    n_boot: int = 1200,
    seed: int = 42,
) -> tuple[float, float]:
    """Moving-block bootstrap CI for overlapping 63d origins.

    Pass ``origin_stride`` so the block length cannot drift out of sync with the
    sampling grid; ``block`` stays available for callers on a non-overlapping
    grid, but supplying both inconsistently is an error rather than a silent win.
    """
    derived = bootstrap_block_for_stride(origin_stride) if origin_stride is not None else None
    if block is None:
        block = derived if derived is not None else 3
    elif derived is not None and block != derived:
        raise ValueError(
            f"block={block} contradicts origin_stride={origin_stride} (expected {derived})"
        )
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
    ci_low, ci_high = _block_bootstrap_hit_ci(hits, origin_stride=origin_stride)
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


def _finite_or_none(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


def _regime_macro_f1(regime: Mapping[str, Any], key: str) -> float:
    """NaN, never 0.0, when a candidate is missing.

    ``or 0.0`` turned an absent regime block into a floor of zero, so a run that
    never scored the regime head passed the non-regression gate trivially.
    """
    block = regime.get(key)
    if not isinstance(block, Mapping):
        return float("nan")
    value = block.get("regime_macro_f1")
    return float(value) if value is not None else float("nan")


def build_gates(
    market_rows: list[dict[str, Any]],
    regime: Mapping[str, Any],
    *,
    include_regime: bool,
    errors: Mapping[str, str] | None = None,
) -> tuple[dict[str, bool], dict[str, Any]]:
    """Promotion gates + the inputs they were evaluated on.

    Pure function so the bar can be unit-tested and so a stored artifact can be
    re-scored against a later spec.
    """
    th = GATE_THRESHOLDS
    errors = errors or {}
    hits = [float(row["direction_hit"]) for row in market_rows]
    ratios = [float(row["mae_ratio"]) for row in market_rows]
    coverages = [float(row["interval_coverage"]) for row in market_rows]
    ci_lows = [float(row["direction_ci_low"]) for row in market_rows]
    # Trivial predictor for a sign task: always call the majority direction.
    trivial = [
        max(float(row["always_up_hit"]), 1.0 - float(row["always_up_hit"]))
        for row in market_rows
    ]
    direction_skill = [hit - base for hit, base in zip(hits, trivial)]

    raw_f1 = _regime_macro_f1(regime, "gbm_unsmoothed")
    temporal_f1 = _regime_macro_f1(regime, "gbm_temporal")
    candidate_f1 = _regime_macro_f1(regime, "ensemble_direct")
    # No * 0.98 slack: "non-regression" has to mean the candidate did not regress.
    finite_floors = [f for f in (raw_f1, temporal_f1) if np.isfinite(f)]
    regime_floor = max(finite_floors) if finite_floors else float("nan")

    gates = {
        "all_markets_completed": bool(
            len(market_rows) >= int(th["min_markets"]) and not errors
        ),
        "median_direction_hit": bool(
            hits and float(np.median(hits)) >= th["median_direction_hit"]
        ),
        "market_floor": bool(hits and min(hits) >= th["market_floor"]),
        "direction_skill_vs_baseline": bool(
            market_rows
            and sum(
                float(row["direction_hit"]) >= float(row["baseline_hit"]) - th["baseline_slack"]
                for row in market_rows
            )
            >= int(th["baseline_min_markets"])
        ),
        # Every market must beat the better constant call. This is the gate that
        # was deleted after it failed; it is the only one that measures skill.
        "direction_skill_vs_trivial": bool(
            market_rows and all(s >= -th["trivial_slack"] for s in direction_skill)
        ),
        # The point estimate is worthless without its lower bound.
        "direction_ci_low_excludes_chance": bool(
            ci_lows and min(ci_lows) >= th["direction_ci_low"]
        ),
        "median_mae_ratio": bool(ratios and float(np.median(ratios)) <= th["median_mae_ratio"]),
        "interval_coverage": bool(
            coverages
            and all(
                th["interval_coverage_min"] <= coverage <= th["interval_coverage_max"]
                for coverage in coverages
            )
        ),
        # Skipping the regime eval is a failure, not a vacuous pass.
        "regime_evaluated": bool(include_regime and np.isfinite(candidate_f1)),
        "regime_macro_f1_non_regression": bool(
            include_regime
            and np.isfinite(candidate_f1)
            and np.isfinite(regime_floor)
            and candidate_f1 >= regime_floor
        ),
    }
    inputs = {
        "median_direction_hit": float(np.median(hits)) if hits else None,
        "min_direction_hit": float(min(hits)) if hits else None,
        "min_direction_ci_low": float(min(ci_lows)) if ci_lows else None,
        "median_mae_ratio": float(np.median(ratios)) if ratios else None,
        "trivial_direction_hit": {
            str(row["symbol"]): base for row, base in zip(market_rows, trivial)
        },
        "direction_skill_vs_trivial": {
            str(row["symbol"]): skill for row, skill in zip(market_rows, direction_skill)
        },
        # None (not 0.0) when absent so the artifact stays honest and JSON-valid.
        "regime_raw_f1": _finite_or_none(raw_f1),
        "regime_temporal_f1": _finite_or_none(temporal_f1),
        "regime_candidate_f1": _finite_or_none(candidate_f1),
        "regime_floor": _finite_or_none(regime_floor),
        "include_regime": bool(include_regime),
    }
    return gates, inputs


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
    gates, gate_inputs = build_gates(
        market_rows, regime, include_regime=include_regime, errors=errors
    )
    passed = all(gates.values())

    hits = [float(row["direction_hit"]) for row in market_rows]
    ratios = [float(row["mae_ratio"]) for row in market_rows]
    candidate_f1 = gate_inputs["regime_candidate_f1"] or 0.0
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
        "gates_failed": [name for name, ok in gates.items() if not ok],
        "gate_spec_version": GATE_SPEC_VERSION,
        "gate_thresholds": dict(GATE_THRESHOLDS),
        "gate_inputs": gate_inputs,
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
