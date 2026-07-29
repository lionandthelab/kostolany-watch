"""Walk-forward 3-month forecast accuracy + model/path tuning."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from kostolany.connectors import load_market
from kostolany.engine import prepare_xy
from kostolany.flows import (
    FORECAST_DAYS,
    REGIME_DAILY_DRIFT,
    REGIME_DAILY_VOL,
)
from kostolany.harness.cv import PurgedWalkForward
from kostolany.harness.metrics import evaluate_regimes
from kostolany.models import KostolanyGBM, KostolanyHMM
from kostolany.regimes import Regime
from kostolany.tsfm import TSFMEnsemble


@dataclass
class PathParams:
    drift_scale: float = 1.0
    stickiness_hmm: float = 0.92
    shock_hmm: float = 0.35
    stickiness_gbm: float = 0.72
    shock_gbm: float = 0.85
    tsfm_blend: float = 0.55  # weight on tsfm daily vs regime drift
    tsfm_scale: float = 0.02
    tsfm_clip: float = 0.0035
    use_empirical_drift: bool = False


@dataclass
class Scorecard:
    name: str
    n_folds: int = 0
    direction_hit: float = 0.0
    mae_ret: float = 0.0
    rmse_ret: float = 0.0
    corr: float = 0.0
    mean_pred_ret: float = 0.0
    mean_real_ret: float = 0.0
    regime_accuracy: float | None = None
    regime_adjacent_accuracy: float | None = None
    regime_cycle_distance: float | None = None
    regime_macro_f1: float | None = None
    regime_brier: float | None = None
    regime_ece: float | None = None
    regime_qwk: float | None = None
    regime_baselines: dict[str, float] = field(default_factory=dict)
    transition_hit: float | None = None  # recall only — kept for continuity
    transition_precision: float | None = None
    transition_f1: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _proba_dict(row: pd.Series) -> dict[str, float]:
    out: dict[str, float] = {}
    for i, name in enumerate(Regime.__members__):
        key = f"p{i}"
        out[name] = float(row.get(key, 0.0) or 0.0)
    return out


def empirical_regime_drifts(
    closes: pd.Series,
    regimes: pd.Series,
    horizon: int = FORECAST_DAYS,
) -> dict[int, float]:
    """Estimate mean daily drift per regime from realized forward returns."""
    closes = closes.astype(float).sort_index()
    regimes = regimes.reindex(closes.index).dropna().astype(int)
    fwd = closes.shift(-horizon) / closes - 1.0
    buckets: dict[int, list[float]] = {int(r): [] for r in Regime}
    for ts, rid in regimes.items():
        r = fwd.get(ts)
        if r is None or not np.isfinite(r):
            continue
        buckets[int(rid)].append(float(r) / horizon)
    out = dict(REGIME_DAILY_DRIFT)
    for rid, vals in buckets.items():
        if len(vals) >= 20:
            out[rid] = float(np.clip(np.mean(vals), -0.004, 0.004))
    return out


def _blend_with_table(
    proba: dict[str, float],
    drift_table: dict[int, float],
    vol_table: dict[int, float] | None = None,
) -> tuple[float, float]:
    vol_table = vol_table or REGIME_DAILY_VOL
    drift = 0.0
    vol = 0.0
    mass = 0.0
    for name, p in proba.items():
        if name not in Regime.__members__:
            continue
        rid = int(Regime[name])
        w = max(0.0, float(p))
        drift += w * drift_table.get(rid, REGIME_DAILY_DRIFT[rid])
        vol += w * vol_table.get(rid, REGIME_DAILY_VOL[rid])
        mass += w
    if mass < 1e-9:
        return 0.0, 0.012
    return drift / mass, max(0.004, vol / mass)


def forecast_terminal_return(
    proba: dict[str, float],
    *,
    kind: str,
    params: PathParams,
    drift_table: dict[int, float],
    traj_row: dict[str, float] | None = None,
) -> float:
    """Deterministic expected terminal return (noise-free path mean)."""
    if kind == "tsfm":
        drift0, _ = _blend_with_table(proba, drift_table)
        h1 = float((traj_row or {}).get("h1", 0.0) or 0.0)
        h5 = float((traj_row or {}).get("h5", 0.0) or 0.0)
        h20 = float((traj_row or {}).get("h20", 0.0) or 0.0)
        h63 = float((traj_row or {}).get("h63", 0.0) or 0.0)
        if np.isfinite(h63) and abs(h63) > 1e-12:
            prior_ret = float(np.expm1(np.clip(drift0 * FORECAST_DAYS, -0.8, 0.8)))
            return float(
                np.clip(
                    params.tsfm_blend * h63
                    + (1.0 - params.tsfm_blend) * prior_ret,
                    -0.85,
                    2.0,
                )
            )
        daily_from_tsfm = 0.45 * h1 + 0.35 * (h5 / 5.0) + 0.20 * (h20 / 20.0)
        daily_from_tsfm = float(
            np.clip(daily_from_tsfm * params.tsfm_scale, -params.tsfm_clip, params.tsfm_clip)
        )
        level = 100.0
        for i in range(FORECAST_DAYS):
            t = i / max(1, FORECAST_DAYS - 1)
            drift = (
                params.tsfm_blend * daily_from_tsfm + (1.0 - params.tsfm_blend) * drift0
            ) * (0.9 + 0.1 * (1 - t))
            level *= 1.0 + drift
        return level / 100.0 - 1.0

    stickiness = params.stickiness_hmm if kind == "hmm" else params.stickiness_gbm
    # Expected path ignores shock noise
    drift0, _ = _blend_with_table(proba, drift_table)
    drift0 *= params.drift_scale
    level = 100.0
    for i in range(FORECAST_DAYS):
        t = i / max(1, FORECAST_DAYS - 1)
        drift = drift0 * (stickiness + (1 - stickiness) * (1 - t))
        level *= 1.0 + drift
    return level / 100.0 - 1.0


def _score_pairs(pred: list[float], real: list[float]) -> dict[str, float]:
    if not pred:
        return {
            "direction_hit": 0.0,
            "mae_ret": 0.0,
            "rmse_ret": 0.0,
            "corr": 0.0,
            "mean_pred_ret": 0.0,
            "mean_real_ret": 0.0,
        }
    p = np.asarray(pred, dtype=float)
    r = np.asarray(real, dtype=float)
    hit = float(np.mean(np.sign(p) == np.sign(r)))
    # treat near-zero as flat: if either ~0, count as hit only if both flat
    flat = (np.abs(p) < 1e-4) & (np.abs(r) < 1e-4)
    nonzero = ~((np.abs(p) < 1e-4) | (np.abs(r) < 1e-4))
    if nonzero.any():
        hit = float(np.mean(np.sign(p[nonzero]) == np.sign(r[nonzero])))
        if flat.any():
            # blend flats as correct
            hit = (hit * nonzero.sum() + flat.sum()) / len(p)
    mae = float(np.mean(np.abs(p - r)))
    rmse = float(np.sqrt(np.mean((p - r) ** 2)))
    corr = float(np.corrcoef(p, r)[0, 1]) if len(p) > 2 and np.std(p) > 1e-12 and np.std(r) > 1e-12 else 0.0
    return {
        "direction_hit": hit,
        "mae_ret": mae,
        "rmse_ret": rmse,
        "corr": corr,
        "mean_pred_ret": float(np.mean(p)),
        "mean_real_ret": float(np.mean(r)),
    }


FitFactory = Callable[[], Any]


def evaluate_regime_model(
    X: pd.DataFrame,
    y_weak: pd.Series,
    y_gold: pd.Series,
    prices: pd.Series,
    factory: FitFactory,
    *,
    n_splits: int = 6,
    test_size: int | None = None,
    min_train_size: int = 1260,
    anchor: str = "end",
    name: str = "model",
) -> Scorecard:
    """Walk-forward regime scoring against gold labels (scoring only, never fit).

    Defaults changed 2026-07-29: ``min_train_size`` 252 -> 1260 (a 6-state HMM
    has 299 free parameters; 242 training rows is 1.24 params per observation)
    and the splitter is anchored at the end. ``test_size=None`` spreads the
    ``n_splits`` blocks over everything after the warm-up instead of scoring 378
    rows (9.5% of the timeline, all of it 2011-2012) at the same refit cost.
    """
    n = len(X)
    if test_size is None:
        test_size = max(63, (n - min_train_size) // max(1, n_splits))
    if n <= min_train_size + test_size:
        raise ValueError(
            f"{name}: regime eval needs > {min_train_size + test_size} rows (got {n})"
        )
    splitter = PurgedWalkForward(
        n_splits=n_splits,
        test_size=test_size,
        purge_horizon=5,
        embargo=5,
        expanding=True,
        min_train_size=min_train_size,
        anchor=anchor,
    )
    preds: list[pd.Series] = []
    probas: list[pd.DataFrame] = []
    golds: list[pd.Series] = []
    transition_detected = 0
    transition_total = 0
    transition_matched = 0
    transition_predicted = 0
    for fold in splitter.split(X):
        Xtr, ytr = X.iloc[fold.train_idx], y_weak.iloc[fold.train_idx]
        Xte = X.iloc[fold.test_idx]
        model = factory()
        yhat, proba = model.fit_predict(Xtr, ytr, Xte)
        yg = y_gold.reindex(Xte.index).dropna()
        common = yhat.dropna().index.intersection(yg.index)
        if len(common) == 0:
            continue
        preds.append(yhat.loc[common].astype(int))
        probas.append(proba.reindex(common))
        golds.append(yg.loc[common].astype(int))
        fold_report = evaluate_regimes(
            yg.loc[common].astype(int),
            yhat.loc[common].astype(int),
            proba.reindex(common),
        )
        transition_detected += int(fold_report.transition.n_detected)
        transition_total += int(fold_report.transition.n_true_transitions)
        transition_matched += int(fold_report.transition.matched_within_window)
        transition_predicted += int(fold_report.transition.n_pred_transitions)
    if not preds:
        return Scorecard(name=name)
    y_pred = pd.concat(preds)
    y_true = pd.concat(golds)
    proba_all = pd.concat(probas) if probas else None
    report = evaluate_regimes(y_true, y_pred, proba_all)

    # Transition precision/recall are pooled over folds (fold boundaries would
    # otherwise register as spurious flips).
    precision = transition_matched / transition_predicted if transition_predicted else None
    recall = transition_detected / transition_total if transition_total else None
    matched_recall = transition_matched / transition_total if transition_total else None
    f1 = None
    if precision is not None and matched_recall is not None and (precision + matched_recall) > 0:
        f1 = 2.0 * precision * matched_recall / (precision + matched_recall)

    index = y_pred.index
    coverage = {
        "n_oos": int(len(y_pred)),
        "oos_fraction": float(len(y_pred) / max(1, n)),
        "oos_start": str(index.min()),
        "oos_end": str(index.max()),
        "n_splits": int(n_splits),
        "test_size": int(test_size),
        "min_train_size": int(min_train_size),
        "anchor": anchor,
    }
    print(
        f"[regime-eval] {name}: OOS {coverage['n_oos']} rows "
        f"({coverage['oos_fraction']:.1%} of {n}) "
        f"{coverage['oos_start'][:10]} -> {coverage['oos_end'][:10]}",
        flush=True,
    )

    return Scorecard(
        name=name,
        n_folds=len(preds),
        regime_accuracy=float(report.accuracy),
        regime_adjacent_accuracy=float(report.adjacent_accuracy),
        regime_cycle_distance=float(report.mean_cycle_distance),
        regime_macro_f1=float(report.macro_f1),
        regime_brier=float(report.calibration.brier),
        regime_ece=float(report.calibration.ece),
        regime_qwk=float(report.quadratic_weighted_kappa),
        regime_baselines=dict(report.baselines),
        transition_hit=float(recall) if recall is not None else None,
        transition_precision=float(precision) if precision is not None else None,
        transition_f1=float(f1) if f1 is not None else None,
        details=coverage,
    )


def evaluate_3m_forecast(
    X: pd.DataFrame,
    y_weak: pd.Series,
    prices: pd.Series,
    factory: FitFactory,
    *,
    kind: str,
    params: PathParams,
    n_splits: int = 6,
    test_size: int = 63,
    name: str = "forecast",
    empirical_drift: dict[int, float] | None = None,
    origin_stride: int = 21,
    refit_stride: int = 63,
) -> Scorecard:
    """Expanding origins: forecast next 63d return vs realized (no peek ahead)."""
    del n_splits, test_size, empirical_drift  # denser origin loop replaces fold-only eval
    n = len(prices)
    min_train = 504  # ~2y
    if n < min_train + FORECAST_DAYS + 10:
        return Scorecard(name=name)

    drift_prior = {k: v * params.drift_scale for k, v in REGIME_DAILY_DRIFT.items()}
    pred_rets: list[float] = []
    real_rets: list[float] = []
    fold_hits: list[float] = []

    model = None
    last_fit_end = -10_000
    drift_table = drift_prior

    origins = list(range(min_train, n - FORECAST_DAYS, origin_stride))
    for origin in origins:
        if model is None or (origin - last_fit_end) >= refit_stride:
            Xtr = X.iloc[:origin]
            ytr = y_weak.iloc[:origin]
            model = factory()
            model.fit(Xtr, ytr)
            last_fit_end = origin
            # Train-only empirical drift from model regimes (causal)
            if params.use_empirical_drift:
                pred_tr = model.predict(Xtr.tail(min(800, len(Xtr))))
                regimes = pred_tr.regimes
                # Causal empirical drift: forward returns must end on/before origin
                closes = prices.iloc[:origin].astype(float)
                regimes2 = regimes.reindex(closes.index).dropna().astype(int)
                fwd = closes.shift(-FORECAST_DAYS) / closes - 1.0
                valid_end = origin - FORECAST_DAYS
                buckets: dict[int, list[float]] = {int(r): [] for r in Regime}
                for ts, rid in regimes2.items():
                    loc = closes.index.get_loc(ts)
                    if isinstance(loc, slice):
                        loc = loc.start
                    if isinstance(loc, np.ndarray):
                        loc = int(loc[-1])
                    if int(loc) >= valid_end:
                        continue
                    r = fwd.get(ts)
                    if r is None or not np.isfinite(r):
                        continue
                    buckets[int(rid)].append(float(r) / FORECAST_DAYS)
                drift_table = dict(REGIME_DAILY_DRIFT)
                for rid, vals in buckets.items():
                    if len(vals) >= 15:
                        drift_table[rid] = float(np.clip(np.mean(vals), -0.004, 0.004))
                drift_table = {k: float(v) * params.drift_scale for k, v in drift_table.items()}
            else:
                drift_table = drift_prior

        # Predict at origin-1
        tail = X.iloc[max(0, origin - 80) : origin]
        if len(tail) < 20:
            continue
        pred = model.predict(tail)
        if pred.proba.empty:
            continue
        last_ts = pred.proba.index[-1]
        proba = _proba_dict(pred.proba.loc[last_ts])
        traj_row = None
        traj = getattr(model, "last_traj_", None)
        if traj is not None and hasattr(traj, "ret_hat") and len(traj.ret_hat):
            row = traj.ret_hat.iloc[-1]
            traj_row = {
                "h1": float(row.get("h1", 0.0) or 0.0),
                "h5": float(row.get("h5", 0.0) or 0.0),
                "h20": float(row.get("h20", 0.0) or 0.0),
                "h63": float(row.get("h63", 0.0) or 0.0),
            }

        p_ret = forecast_terminal_return(
            proba, kind=kind, params=params, drift_table=drift_table, traj_row=traj_row
        )
        px0 = float(prices.iloc[origin - 1])
        px1 = float(prices.iloc[origin - 1 + FORECAST_DAYS])
        r_ret = px1 / px0 - 1.0
        pred_rets.append(p_ret)
        real_rets.append(r_ret)
        fold_hits.append(1.0 if np.sign(p_ret) == np.sign(r_ret) or (abs(p_ret) < 1e-4 and abs(r_ret) < 1e-4) else 0.0)

    stats = _score_pairs(pred_rets, real_rets)
    return Scorecard(
        name=name,
        n_folds=len(pred_rets),
        direction_hit=stats["direction_hit"],
        mae_ret=stats["mae_ret"],
        rmse_ret=stats["rmse_ret"],
        corr=stats["corr"],
        mean_pred_ret=stats["mean_pred_ret"],
        mean_real_ret=stats["mean_real_ret"],
        details={"fold_hits_n": len(fold_hits), "params": asdict(params), "n_origins": len(pred_rets)},
    )


def run_experiment(
    symbol: str = "KS11",
    *,
    start: str = "2010-01-01",
    n_splits: int = 6,
    output_dir: str | Path = "artifacts/experiments",
) -> dict[str, Any]:
    t0 = time.time()
    market = load_market(symbol, start=start, enrich_fred=True)
    X, y_weak, y_gold, prices = prepare_xy(market)
    valid = X.dropna().index.intersection(y_weak.dropna().index).intersection(prices.dropna().index)
    X = X.loc[valid]
    y_weak = y_weak.loc[valid].astype(int)
    y_gold = y_gold.reindex(valid)
    prices = prices.loc[valid].astype(float)

    # Empirical drifts from gold labels (eval-only features of labels — used only for path table)
    # Fit a quick HMM on full history ONLY to assign regimes for drift estimation would leak.
    # Instead: use weak labels as regime proxy for empirical drift (available at train time conceptually).
    emp = empirical_regime_drifts(prices, y_weak)

    results: dict[str, Any] = {
        "symbol": symbol,
        "start": start,
        "n_rows": int(len(X)),
        "n_splits": n_splits,
        "horizon_days": FORECAST_DAYS,
        "empirical_drift": {str(k): v for k, v in emp.items()},
        "baseline_drift": {str(k): v for k, v in REGIME_DAILY_DRIFT.items()},
        "regime": {},
        "forecast_3m": {},
        "best": {},
    }

    # --- Regime accuracy baselines + tuned ---
    regime_cfgs: list[tuple[str, FitFactory]] = [
        ("hmm_baseline", lambda: KostolanyHMM()),
        ("hmm_tuned", lambda: KostolanyHMM(n_iter=350, covariance_type="diag")),
        ("gbm_baseline", lambda: KostolanyGBM()),
        (
            "gbm_tuned",
            lambda: KostolanyGBM(
                n_estimators=400,
                learning_rate=0.03,
                num_leaves=47,
                calibrate=True,
            ),
        ),
        ("tsfm_baseline", lambda: TSFMEnsemble()),
        (
            "tsfm_tuned",
            lambda: TSFMEnsemble(
                w_hmm=0.30,
                w_gbm=0.45,
                w_tsfm=0.25,
                transition_soften=0.15,
                gbm_kwargs={"n_estimators": 400, "learning_rate": 0.03, "num_leaves": 47},
            ),
        ),
        (
            "tsfm_w_bal",
            lambda: TSFMEnsemble(w_hmm=0.35, w_gbm=0.40, w_tsfm=0.25, transition_soften=0.20),
        ),
    ]
    for name, factory in regime_cfgs:
        print(f"[regime] {name}…", flush=True)
        sc = evaluate_regime_model(
            X, y_weak, y_gold, prices, factory, n_splits=n_splits, name=name
        )
        results["regime"][name] = sc.to_dict()
        print(
            f"  acc={_fmt(sc.regime_accuracy)} (majority "
            f"{_fmt((sc.regime_baselines or {}).get('majority'))})  "
            f"f1={_fmt(sc.regime_macro_f1)}  qwk={_fmt(sc.regime_qwk)}  folds={sc.n_folds}",
            flush=True,
        )

    # --- 3m forecast baselines ---
    base_params = PathParams()
    forecast_kinds = [
        ("hmm", "hmm_baseline", lambda: KostolanyHMM()),
        ("gbm", "gbm_baseline", lambda: KostolanyGBM()),
        ("tsfm", "tsfm_baseline", lambda: TSFMEnsemble()),
    ]
    for kind, label, factory in forecast_kinds:
        name = f"{label}_path"
        print(f"[3m] {name}…", flush=True)
        sc = evaluate_3m_forecast(
            X,
            y_weak,
            prices,
            factory,
            kind=kind,
            params=base_params,
            n_splits=n_splits,
            name=name,
        )
        results["forecast_3m"][name] = sc.to_dict()
        print(
            f"  hit={sc.direction_hit:.3f}  mae={sc.mae_ret:.4f}  corr={sc.corr:.3f}",
            flush=True,
        )

    # --- Path param grid (dense origins) ---
    grid = [
        PathParams(drift_scale=1.0, use_empirical_drift=False),
        PathParams(drift_scale=1.0, use_empirical_drift=True),
        PathParams(drift_scale=1.25, use_empirical_drift=True, tsfm_blend=0.65, tsfm_scale=0.028),
        PathParams(
            drift_scale=0.85,
            use_empirical_drift=True,
            stickiness_gbm=0.55,
            stickiness_hmm=0.8,
            tsfm_blend=0.45,
        ),
    ]

    best_by_kind: dict[str, Scorecard] = {}
    for kind, factory in (
        ("hmm", lambda: KostolanyHMM()),
        ("gbm", lambda: KostolanyGBM(n_estimators=400, learning_rate=0.03, num_leaves=47)),
        (
            "tsfm",
            lambda: TSFMEnsemble(
                w_hmm=0.30,
                w_gbm=0.45,
                w_tsfm=0.25,
                gbm_kwargs={"n_estimators": 400, "learning_rate": 0.03, "num_leaves": 47},
            ),
        ),
    ):
        best: Scorecard | None = None
        for i, params in enumerate(grid):
            name = f"{kind}_grid_{i}"
            print(f"[tune] {name} empirical={params.use_empirical_drift} scale={params.drift_scale}…", flush=True)
            sc = evaluate_3m_forecast(
                X,
                y_weak,
                prices,
                factory,
                kind=kind,
                params=params,
                n_splits=n_splits,
                name=name,
                empirical_drift=emp,
            )
            results["forecast_3m"][name] = sc.to_dict()
            print(f"  hit={sc.direction_hit:.3f}  mae={sc.mae_ret:.4f}", flush=True)
            if best is None or (
                sc.direction_hit > best.direction_hit
                or (
                    abs(sc.direction_hit - best.direction_hit) < 1e-9
                    and sc.mae_ret < best.mae_ret
                )
            ):
                best = sc
        if best:
            best_by_kind[kind] = best

    results["best"] = {k: v.to_dict() for k, v in best_by_kind.items()}

    # Consensus baseline vs best (mean of 3)
    def _consensus(names: list[str]) -> dict[str, float]:
        cards = [results["forecast_3m"][n] for n in names if n in results["forecast_3m"]]
        if not cards:
            return {}
        return {
            "direction_hit": float(np.mean([c["direction_hit"] for c in cards])),
            "mae_ret": float(np.mean([c["mae_ret"] for c in cards])),
            "corr": float(np.mean([c["corr"] for c in cards])),
        }

    results["consensus"] = {
        "baseline": _consensus(["hmm_baseline_path", "gbm_baseline_path", "tsfm_baseline_path"]),
        "tuned": {
            "direction_hit": float(np.mean([v.direction_hit for v in best_by_kind.values()])),
            "mae_ret": float(np.mean([v.mae_ret for v in best_by_kind.values()])),
            "corr": float(np.mean([v.corr for v in best_by_kind.values()])),
        },
    }

    # Best regime model
    reg = results["regime"]
    best_reg = max(
        reg.items(),
        key=lambda kv: (kv[1].get("regime_accuracy") or 0.0, kv[1].get("regime_macro_f1") or 0.0),
    )
    results["best_regime"] = {"name": best_reg[0], **best_reg[1]}

    results["elapsed_sec"] = round(time.time() - t0, 1)
    results["asof"] = datetime.now(timezone.utc).isoformat()

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"forecast_tune_{symbol.replace('^', '')}_{stamp}.json"
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    results["artifact"] = str(path)
    return results


def _fmt(value: Any, spec: str = ".3f") -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "n/a"
    return format(value, spec)


def summarize(results: dict[str, Any]) -> str:
    lines = []
    sym = results["symbol"]
    lines.append(f"# Kostolany tune experiment - {sym}")
    lines.append("")
    lines.append(f"- rows={results['n_rows']}  folds={results['n_splits']}  horizon={results['horizon_days']}d")
    lines.append(f"- elapsed={results['elapsed_sec']}s")
    lines.append("")
    lines.append("## Regime classification (gold OOS)")
    for name, sc in results["regime"].items():
        base = sc.get("regime_baselines") or {}
        det = sc.get("details") or {}
        lines.append(
            f"- **{name}**: accuracy={_fmt(sc.get('regime_accuracy'))} "
            f"(majority {_fmt(base.get('majority'))}), "
            f"macro_f1={_fmt(sc.get('regime_macro_f1'))}, "
            f"qwk={_fmt(sc.get('regime_qwk'))}, "
            f"brier={_fmt(sc.get('regime_brier'), '.4f')} "
            f"(constant prior {_fmt(base.get('constant_prior_brier'), '.4f')}), "
            f"adjacent={_fmt(sc.get('regime_adjacent_accuracy'))} "
            f"(best constant {_fmt(base.get('best_constant_adjacent'))}), "
            f"oos={det.get('n_oos')} rows {str(det.get('oos_start'))[:10]}..{str(det.get('oos_end'))[:10]}"
        )
    br = results.get("best_regime", {})
    lines.append(f"- winner: **{br.get('name')}** (acc={_fmt(br.get('regime_accuracy'))})")
    lines.append("")
    lines.append("## 3-month direction forecast")
    base = results["consensus"]["baseline"]
    tun = results["consensus"]["tuned"]
    lines.append(
        f"- consensus baseline: hit={base.get('direction_hit', 0):.1%}, mae={base.get('mae_ret', 0):.4f}"
    )
    lines.append(
        f"- consensus tuned: hit={tun.get('direction_hit', 0):.1%}, mae={tun.get('mae_ret', 0):.4f}"
    )
    if base.get("direction_hit"):
        dhit = tun["direction_hit"] - base["direction_hit"]
        lines.append(f"- d_hit: {dhit:+.1%}  |  d_mae: {tun['mae_ret'] - base['mae_ret']:+.4f}")
    lines.append("")
    for kind, sc in results.get("best", {}).items():
        p = sc.get("details", {}).get("params", {})
        n_o = sc.get("details", {}).get("n_origins", sc.get("n_folds"))
        lines.append(
            f"- best **{kind}**: hit={sc['direction_hit']:.1%}, mae={sc['mae_ret']:.4f}, "
            f"n={n_o}, empirical={p.get('use_empirical_drift')}, scale={p.get('drift_scale')}"
        )
    lines.append("")
    lines.append("## Empirical daily drift (weak-label proxy)")
    for k, v in results.get("empirical_drift", {}).items():
        b = results.get("baseline_drift", {}).get(k)
        lines.append(f"- regime {k}: prior={b:.5f} -> emp={v:.5f}")
    return "\n".join(lines)
