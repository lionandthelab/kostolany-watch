"""Walk-forward comparison of the continuous PhaseHead against the shipped stack.

Scores every candidate on the SAME folds against the gold sector derived from
``phase.gold_phase_angle``. Gold is EVAL ONLY — nothing here reaches a fit().

Bootstrap CIs resample LEGS, not bars: bars inside one leg are not independent,
so a bar bootstrap would understate the CI by roughly sqrt(bars_per_leg).

Usage:
  .\\.venv\\Scripts\\python.exe scripts/run_phase_experiment.py
  .\\.venv\\Scripts\\python.exe scripts/run_phase_experiment.py --symbol KS11 --n-splits 8
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kostolany.connectors.cache import read_cache  # noqa: E402
from kostolany.data import MarketData  # noqa: E402
from kostolany.engine import prepare_xy  # noqa: E402
from kostolany.harness.cv import PurgedWalkForward  # noqa: E402
from kostolany.harness.metrics import evaluate_regimes  # noqa: E402
from kostolany.anchor import apply_lambda, fit_lambda  # noqa: E402
from kostolany.labels import gold_labels  # noqa: E402
from kostolany.labels_pit import clock_terciles, clock_third, pit_state  # noqa: E402
from kostolany.models import EnsembleEngine, KostolanyGBM  # noqa: E402
from kostolany.tsfm import TSFMEnsemble  # noqa: E402

# Pre-registered momentum-floor family (side_head_v1.json). Selection-free:
# the MEDIAN of the family is the primary comparator, the MAX the guardrail.
MOMO_MA_WINDOWS = (20, 40, 60, 100, 200)
MOMO_RET_HORIZONS = (10, 20, 60)
from kostolany.phase import (  # noqa: E402
    DEFAULT_PHASE_FEATURES,
    PhaseHead,
    gold_leg_segments,
    gold_phase_sectors,
)
from kostolany.regimes import DISCLAIMER_KO  # noqa: E402

# Shared baseline battery lives in metrics.py as ``regime_baselines``. Imported
# defensively so this script still runs against an older harness; when present it
# is recorded alongside the local baselines so the two lanes cannot silently
# disagree about what "the trivial baseline" is.
try:  # pragma: no cover - depends on the harness lane
    from kostolany.harness.metrics import regime_baselines as metrics_baselines  # type: ignore
except ImportError:  # pragma: no cover
    metrics_baselines = None

OHLCV_COLS = ["open", "high", "low", "close", "volume"]
N_CLASSES = 6


# --------------------------------------------------------------------- data


def load_market_cached(symbol: str, start: str, *, enrich_fred: bool = False) -> MarketData:
    """Read the on-disk connector cache so the run needs no network.

    Mirrors ``connectors.krx.fetch_krx``'s cache layout
    (``artifacts/cache/krx_ks11_{start}.parquet``) instead of inventing a loader.
    """
    key = symbol.upper().lstrip("^")
    if key == "KS11":
        cached = read_cache(f"krx_ks11_{start}", max_age_hours=None)
        if cached is not None and not cached.empty:
            extra_cols = [c for c in cached.columns if c not in OHLCV_COLS]
            extras = cached[extra_cols] if extra_cols else None
            return MarketData(symbol="KS11", ohlcv=cached[OHLCV_COLS], extras=extras)
    from kostolany.connectors import load_market

    return load_market(symbol, start=start, enrich_fred=enrich_fred)


# ------------------------------------------------------------------ metrics


def _ece(y_true: np.ndarray, proba: np.ndarray, n_bins: int = 10) -> float:
    conf = proba.max(axis=1)
    pred = proba.argmax(axis=1)
    correct = (pred == y_true).astype(float)
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    total = 0.0
    for i in range(n_bins):
        m = (conf > bins[i]) & (conf <= bins[i + 1])
        if not np.any(m):
            continue
        total += abs(correct[m].mean() - conf[m].mean()) * m.mean()
    return float(total)


def _macro_f1(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    f1s = []
    for k in range(N_CLASSES):
        tp = float(np.sum((y_pred == k) & (y_true == k)))
        fp = float(np.sum((y_pred == k) & (y_true != k)))
        fn = float(np.sum((y_pred != k) & (y_true == k)))
        denom = 2 * tp + fp + fn
        f1s.append(0.0 if denom == 0 else 2 * tp / denom)
    return float(np.mean(f1s))


def score_arrays(y_true: np.ndarray, y_pred: np.ndarray, proba: np.ndarray) -> dict[str, float]:
    diff = np.abs(y_true - y_pred)
    cyc = np.minimum(diff, N_CLASSES - diff)
    onehot = np.eye(N_CLASSES, dtype=float)[y_true]
    return {
        "exact6": float(np.mean(y_true == y_pred)),
        "adjacent": float(np.mean(cyc <= 1)),
        "mean_cycle_distance": float(np.mean(cyc)),
        "macro_f1": _macro_f1(y_true, y_pred),
        "brier": float(np.mean(np.mean((proba - onehot) ** 2, axis=0))),
        "ece": _ece(y_true, proba, 10),
    }


def factorisation(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """SIDE x THIRD decomposition — the plumbing tripwire.

    exact6 == P(side right) * P(third right | side right) is an identity, so a
    material gap means predictions and truth are misaligned somewhere.
    """
    side_true = (y_true >= 3).astype(int)
    side_pred = (y_pred >= 3).astype(int)
    side_ok = side_true == side_pred
    side_acc = float(np.mean(side_ok))
    if side_ok.sum() == 0:
        third_acc = float("nan")
    else:
        third_acc = float(np.mean((y_true % 3)[side_ok] == (y_pred % 3)[side_ok]))
    exact6 = float(np.mean(y_true == y_pred))
    gap = abs(side_acc * third_acc - exact6) if np.isfinite(third_acc) else float("nan")
    return {
        "side_accuracy": side_acc,
        "third_accuracy_given_side": third_acc,
        "exact6": exact6,
        "product_gap": float(gap),
        "ok": bool(np.isfinite(gap) and gap <= 0.02),
    }


# ---------------------------------------------------------------- baselines


def _constant_proba(class_id: int, n: int, eps: float = 1e-6) -> np.ndarray:
    p = np.full((n, N_CLASSES), eps, dtype=float)
    p[:, class_id] = 1.0 - eps * (N_CLASSES - 1)
    return p


def _onehot_proba(y: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    p = np.full((len(y), N_CLASSES), eps, dtype=float)
    p[np.arange(len(y)), y] = 1.0 - eps * (N_CLASSES - 1)
    return p


def _prior_vector(y_train: np.ndarray) -> np.ndarray:
    counts = np.bincount(y_train, minlength=N_CLASSES).astype(float) + 0.5
    return counts / counts.sum()


# ------------------------------------------------------------------- folds


def build_folds(
    n_rows: int, n_splits: int, min_train: int, anchor: str | None
) -> list[Any]:
    test_size = max(21, (n_rows - min_train) // max(n_splits, 1))
    kwargs: dict[str, Any] = dict(
        n_splits=n_splits,
        min_train_size=min_train,
        test_size=test_size,
        purge_horizon=5,
        embargo=5,
        expanding=True,
    )
    params = inspect.signature(PurgedWalkForward.__init__).parameters
    if anchor is not None and "anchor" in params:
        kwargs["anchor"] = anchor
    elif anchor is not None:
        print(f"[warn] PurgedWalkForward has no 'anchor' parameter yet; ignoring --anchor {anchor}")
    cv = PurgedWalkForward(**kwargs)
    return list(cv.split(np.zeros((n_rows, 1))))


# --------------------------------------------------------------------- run


def run(args: argparse.Namespace) -> dict[str, Any]:
    t0 = time.time()
    market = load_market_cached(args.symbol, args.start, enrich_fred=args.enrich_fred)
    X, y_weak, _y_gold_labels, prices = prepare_xy(market)

    valid = (
        X.dropna().index.intersection(y_weak.dropna().index).intersection(prices.dropna().index)
    )
    X = X.loc[valid]
    y_weak = y_weak.loc[valid].astype(int)
    prices = prices.loc[valid].astype(float)

    # EVAL ONLY targets, computed after the causal training frame is fixed.
    gold_sector = gold_phase_sectors(prices, min_cycle=args.min_cycle).reindex(valid)
    legacy_gold = gold_labels(prices, min_cycle=args.min_cycle).reindex(valid)
    gold_agreement = float((gold_sector == legacy_gold).mean())
    segments = gold_leg_segments(prices, min_cycle=args.min_cycle).reindex(valid)

    folds = build_folds(len(X), args.n_splits, args.min_train, args.anchor)
    print(
        f"[setup] rows={len(X)} folds={len(folds)} "
        f"gold_sector_vs_gold_labels_agreement={gold_agreement:.4f}",
        flush=True,
    )

    alpha = args.alpha
    alpha_tuned = False
    if args.tune_alpha:
        # Tuned ONCE on the earliest fold's training slice, then frozen for every
        # fold. Per-market/per-fold tuning is exactly the overfitting this whole
        # plan exists to stop.
        alpha = _tune_alpha_once(X.iloc[folds[0].train_idx], y_weak.iloc[folds[0].train_idx])
        alpha_tuned = True
        print(f"[setup] alpha tuned once on fold 0 train slice -> {alpha}", flush=True)

    candidates = ["gbm_shipped", "phase_head", "majority_weak", "weak_label", "prior_shrunk"]
    if not args.skip_ensemble:
        candidates.insert(1, "ensemble_shipped")
    # The three arms the watch desk actually serves. `_build_watch_body` routes
    # the default `hmm,gbm,tsfm` request through `fit_analyst_bundle`, so:
    #   hmm_shipped  = KostolanyHMM() defaults        (리듬이)
    #   gbm_serving  = KostolanyGBM(cycle_smooth=0.0)  (눈치왕 — TSFMEnsemble arm,
    #                  NOT the standalone gbm_shipped config above)
    #   tsfm_shipped = TSFMEnsemble.predict            (파도꾼)
    # One TSFMEnsemble fit per fold yields all three, mirroring the bundle path.
    # *_anchored = the same arms after the one-scalar lambda-uniform anchor the
    # serving path now applies (G14 measurement; argmax is anchor-invariant).
    if not args.skip_serving_arms:
        candidates[0:0] = [
            "hmm_shipped",
            "gbm_serving",
            "tsfm_shipped",
            "hmm_anchored",
            "gbm_anchored",
            "tsfm_anchored",
        ]
    # Pre-registered comparator layer (side_head_v1.json): uniform floor,
    # clock-rolled hedge, and the 8-rule momentum family with the causal clock.
    momo_names = [f"momo_ma{w}" for w in MOMO_MA_WINDOWS] + [
        f"momo_ret{h}" for h in MOMO_RET_HORIZONS
    ]
    candidates += ["uniform", "clockroll_hedged", *momo_names]

    # Causal, prefix-stable inputs shared by the comparator arms.
    clock = pit_state(prices)
    momo_up: dict[str, pd.Series] = {}
    for w in MOMO_MA_WINDOWS:
        momo_up[f"momo_ma{w}"] = prices > prices.rolling(w, min_periods=w // 2).mean()
    for h in MOMO_RET_HORIZONS:
        momo_up[f"momo_ret{h}"] = prices.pct_change(h) > 0

    preds: dict[str, list[pd.Series]] = {c: [] for c in candidates}
    probas: dict[str, list[pd.DataFrame]] = {c: [] for c in candidates}
    kappas: list[float] = []
    lambdas: list[dict[str, float]] = []
    fold_meta: list[dict[str, Any]] = []
    warnings: list[str] = []

    cols = [f"p{i}" for i in range(N_CLASSES)]
    for fold in folds:
        Xtr, ytr = X.iloc[fold.train_idx], y_weak.iloc[fold.train_idx]
        Xte = X.iloc[fold.test_idx]
        idx = Xte.index
        print(
            f"[fold {fold.fold_id}] train={len(Xtr)} test={len(Xte)} "
            f"{idx[0].date()}..{idx[-1].date()}",
            flush=True,
        )
        fold_meta.append(
            {
                "fold_id": int(fold.fold_id),
                "n_train": int(len(Xtr)),
                "n_test": int(len(Xte)),
                "train_start": str(Xtr.index[0].date()),
                "train_end": str(Xtr.index[-1].date()),
                "test_start": str(idx[0].date()),
                "test_end": str(idx[-1].date()),
                "purged": int(fold.purged_count),
                "embargo": int(fold.embargo_count),
            }
        )

        # (a0) the three arms the watch desk serves, extracted from ONE
        # TSFMEnsemble fit per fold exactly as fit_analyst_bundle does — plus
        # their lambda-anchored versions, with lambda fitted CAUSALLY on the
        # trailing 252 train bars via a prefix refit (same recipe as serving).
        if not args.skip_serving_arms:
            serving_names = (
                "hmm_shipped",
                "gbm_serving",
                "tsfm_shipped",
                "hmm_anchored",
                "gbm_anchored",
                "tsfm_anchored",
            )
            try:
                bundle = TSFMEnsemble().fit(Xtr, ytr)
                arm_preds = {
                    "hmm": bundle.hmm.predict(Xte),
                    "gbm": bundle.gbm.predict(Xte),
                    "tsfm": bundle.predict(Xte),
                }
                lam = {"hmm": 0.0, "gbm": 0.0, "tsfm": 0.0}
                hold = 252
                if len(Xtr) - hold >= 504:
                    pre = TSFMEnsemble().fit(Xtr.iloc[:-hold], ytr.iloc[:-hold])
                    Xh, yh = Xtr.iloc[-hold:], ytr.iloc[-hold:]
                    lam = {
                        "hmm": fit_lambda(pre.hmm.predict(Xh).proba[cols], yh),
                        "gbm": fit_lambda(pre.gbm.predict(Xh).proba[cols], yh),
                        "tsfm": fit_lambda(pre.predict(Xh).proba[cols], yh),
                    }
                lambdas.append(lam)
                raw_map = {
                    "hmm_shipped": "hmm",
                    "gbm_serving": "gbm",
                    "tsfm_shipped": "tsfm",
                }
                for cname, aid in raw_map.items():
                    rp = arm_preds[aid]
                    preds[cname].append(rp.regimes.reindex(idx))
                    probas[cname].append(rp.proba.reindex(idx)[cols])
                    # Anchored: argmax invariant, only the probability changes.
                    anch = apply_lambda(rp.proba.reindex(idx)[cols], lam[aid])
                    preds[aid + "_anchored"].append(rp.regimes.reindex(idx))
                    probas[aid + "_anchored"].append(anch)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"serving arms failed on fold {fold.fold_id}: {exc}")
                for cname in serving_names:
                    preds[cname].append(pd.Series(np.nan, index=idx))
                    probas[cname].append(pd.DataFrame(np.nan, index=idx, columns=cols))

        # (a) shipped GBM
        r, p = KostolanyGBM(calibrate=True, cycle_smooth=0.22).fit_predict(Xtr, ytr, Xte)
        preds["gbm_shipped"].append(r.reindex(idx))
        probas["gbm_shipped"].append(p.reindex(idx)[cols])

        # (b) shipped EnsembleEngine (HMM+GBM). Chosen over TSFMEnsemble because
        # LocalTSFM.fit is ~27s/fit vs ~5s here, and TSFMEnsemble contains this
        # ensemble anyway.
        if not args.skip_ensemble:
            try:
                r, p = EnsembleEngine().fit_predict(Xtr, ytr, Xte)
                preds["ensemble_shipped"].append(r.reindex(idx))
                probas["ensemble_shipped"].append(p.reindex(idx)[cols])
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"ensemble_shipped failed on fold {fold.fold_id}: {exc}")
                preds["ensemble_shipped"].append(pd.Series(np.nan, index=idx))
                probas["ensemble_shipped"].append(pd.DataFrame(np.nan, index=idx, columns=cols))

        # (c) phase head
        head = PhaseHead(alpha=alpha)
        r, p = head.fit_predict(Xtr, ytr, Xte)
        kappas.append(float(head.kappa_))
        preds["phase_head"].append(r.reindex(idx))
        probas["phase_head"].append(p.reindex(idx)[cols])

        # (d) trivial baselines, all causal (train-slice statistics only)
        ytr_arr = ytr.to_numpy(dtype=int)
        mode = int(np.bincount(ytr_arr, minlength=N_CLASSES).argmax())
        preds["majority_weak"].append(pd.Series(mode, index=idx))
        probas["majority_weak"].append(
            pd.DataFrame(_constant_proba(mode, len(idx)), index=idx, columns=cols)
        )

        wk = y_weak.reindex(idx).to_numpy(dtype=int)
        preds["weak_label"].append(pd.Series(wk, index=idx))
        probas["weak_label"].append(pd.DataFrame(_onehot_proba(wk), index=idx, columns=cols))

        prior = _prior_vector(ytr_arr)
        preds["prior_shrunk"].append(pd.Series(int(prior.argmax()), index=idx))
        probas["prior_shrunk"].append(
            pd.DataFrame(np.tile(prior, (len(idx), 1)), index=idx, columns=cols)
        )

        # (e) uniform floor: Brier exactly 5/36 whatever the label distribution.
        # argmax of a uniform vector is undefined, so pred = train-prior argmax.
        preds["uniform"].append(pd.Series(int(prior.argmax()), index=idx))
        probas["uniform"].append(
            pd.DataFrame(1.0 / N_CLASSES, index=idx, columns=cols)
        )

        # (f) causal clock: side from confirmed turns, third from TRAIN-fitted
        # tercile cuts of elapsed bars. Zero learned parameters.
        clk_tr = clock.reindex(Xtr.index)
        cuts = clock_terciles(clk_tr.loc[clk_tr["side"] != 0, "k"])
        clk_te = clock.reindex(idx)
        thirds = clock_third(clk_te["k"], cuts)
        clock_class = np.where(
            clk_te["side"].to_numpy() == 0,
            int(prior.argmax()),
            np.where(clk_te["side"].to_numpy() > 0, thirds, 3 + thirds),
        ).astype(int)

        # Train-slice survival rate of the clock's own class (one statistic).
        tr_thirds = clock_third(clk_tr["k"], cuts)
        tr_class = np.where(
            clk_tr["side"].to_numpy() == 0,
            int(prior.argmax()),
            np.where(clk_tr["side"].to_numpy() > 0, tr_thirds, 3 + tr_thirds),
        ).astype(int)
        surv = float(np.mean(tr_class[1:] == tr_class[:-1])) if len(tr_class) > 1 else 0.8
        hedged = np.full((len(idx), N_CLASSES), (1.0 - surv) / (N_CLASSES - 1))
        hedged[np.arange(len(idx)), clock_class] = surv
        preds["clockroll_hedged"].append(pd.Series(clock_class, index=idx))
        probas["clockroll_hedged"].append(pd.DataFrame(hedged, index=idx, columns=cols))

        # (g) momentum-floor family: unfitted side rule x the same clock third.
        for mname in momo_names:
            up = momo_up[mname].reindex(idx).fillna(True).to_numpy()
            mclass = np.where(up, thirds, 3 + thirds).astype(int)
            preds[mname].append(pd.Series(mclass, index=idx))
            probas[mname].append(
                pd.DataFrame(_onehot_proba(mclass), index=idx, columns=cols)
            )

    oos_index = pd.Index(np.concatenate([p.index.to_numpy() for p in preds[candidates[0]]]))
    seg_oos = segments.reindex(oos_index)
    scored_mask = (seg_oos["leg_id"] >= 0).to_numpy()
    scored_index = oos_index[scored_mask]

    y_true = gold_sector.reindex(scored_index).to_numpy(dtype=int)
    leg_of_row = seg_oos.loc[scored_index, "leg_id"].to_numpy(dtype=int)
    leg_ids = np.unique(leg_of_row)
    n_legs = int(len(leg_ids))

    # Oracle "best constant sector": chosen against OOS gold, so it is an upper
    # bound on any constant predictor, not a deployable baseline.
    best_const = int(
        min(
            range(N_CLASSES),
            key=lambda k: float(
                np.mean(np.minimum(np.abs(y_true - k), N_CLASSES - np.abs(y_true - k)))
            ),
        )
    )
    preds["best_constant_oracle"] = [pd.Series(best_const, index=scored_index)]
    probas["best_constant_oracle"] = [
        pd.DataFrame(_constant_proba(best_const, len(scored_index)), index=scored_index, columns=cols)
    ]
    candidates.append("best_constant_oracle")

    stacked_pred: dict[str, np.ndarray] = {}
    stacked_proba: dict[str, np.ndarray] = {}
    for name in candidates:
        s = pd.concat(preds[name]).reindex(scored_index)
        p = pd.concat(probas[name]).reindex(scored_index)
        if s.isna().any():
            warnings.append(f"{name}: {int(s.isna().sum())} NaN predictions dropped to prior")
            s = s.fillna(float(best_const))
            p = p.fillna(1.0 / N_CLASSES)
        stacked_pred[name] = s.to_numpy(dtype=int)
        arr = p.to_numpy(dtype=float)
        stacked_proba[name] = arr / np.clip(arr.sum(axis=1, keepdims=True), 1e-12, None)

    # Point estimates + leg-block bootstrap CIs
    leg_positions = {int(lid): np.flatnonzero(leg_of_row == lid) for lid in leg_ids}
    rng = np.random.default_rng(args.seed)
    draws = rng.integers(0, n_legs, size=(args.n_boot, n_legs))
    boot_index = [
        np.concatenate([leg_positions[int(leg_ids[d])] for d in draws[b]])
        for b in range(args.n_boot)
    ]

    results: dict[str, Any] = {}
    boot_samples: dict[str, dict[str, np.ndarray]] = {}
    for name in candidates:
        yp = stacked_pred[name]
        P = stacked_proba[name]
        point = score_arrays(y_true, yp, P)
        samples: dict[str, list[float]] = {k: [] for k in point}
        for sel in boot_index:
            s = score_arrays(y_true[sel], yp[sel], P[sel])
            for k, v in s.items():
                samples[k].append(v)
        boot_samples[name] = {k: np.asarray(v, dtype=float) for k, v in samples.items()}
        ci = {
            k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))]
            for k, v in samples.items()
        }
        # Cross-check the point estimate against the shared harness reporter.
        report = evaluate_regimes(
            pd.Series(y_true, index=scored_index),
            pd.Series(yp, index=scored_index),
            pd.DataFrame(P, index=scored_index, columns=cols),
        )
        results[name] = {
            **point,
            "ci95_leg_bootstrap": ci,
            "factorisation": factorisation(y_true, yp),
            "harness_crosscheck": {
                "accuracy": report.accuracy,
                "adjacent_accuracy": report.adjacent_accuracy,
                "mean_cycle_distance": report.mean_cycle_distance,
                "macro_f1": report.macro_f1,
                "brier": report.calibration.brier,
                "ece": report.calibration.ece,
            },
        }

    # Paired deltas on the SAME leg resamples. Marginal CIs cannot rank two
    # candidates scored on identical folds — most of their variance is common.
    paired: dict[str, Any] = {}
    for other in candidates:
        if other == "phase_head":
            continue
        block: dict[str, Any] = {}
        for metric in boot_samples["phase_head"]:
            delta = boot_samples["phase_head"][metric] - boot_samples[other][metric]
            lo, hi = float(np.percentile(delta, 2.5)), float(np.percentile(delta, 97.5))
            block[metric] = {
                "delta": float(
                    results["phase_head"][metric] - results[other][metric]
                ),
                "ci95": [lo, hi],
                "excludes_zero": bool(lo > 0.0 or hi < 0.0),
            }
        paired[f"phase_head_minus_{other}"] = block

    trip = results["phase_head"]["factorisation"]
    if not trip["ok"]:
        warnings.append(
            "FACTORISATION TRIPWIRE FAILED for phase_head: "
            f"|side*third - exact6| = {trip['product_gap']:.4f} > 0.02. "
            "The plumbing is wrong and this run is NOT quotable."
        )
        print("\n" + "!" * 78, flush=True)
        print("!! FACTORISATION TRIPWIRE FAILED - RUN IS NOT QUOTABLE", flush=True)
        print(f"!! side={trip['side_accuracy']:.4f} third|side={trip['third_accuracy_given_side']:.4f} "
              f"exact6={trip['exact6']:.4f} gap={trip['product_gap']:.4f}", flush=True)
        print("!" * 78 + "\n", flush=True)
    if n_legs < 40:
        warnings.append(
            f"ONLY {n_legs} INDEPENDENT LEGS IN THE OOS WINDOW — leg-bootstrap CIs are "
            "wide and any ranking between candidates is weakly identified."
        )
        print(
            f"\n[LOUD] only {n_legs} independent legs in OOS - CIs are barely informative\n",
            flush=True,
        )

    payload: dict[str, Any] = {
        "symbol": args.symbol,
        "start": args.start,
        "asof": datetime.now(timezone.utc).isoformat(),
        "elapsed_sec": round(time.time() - t0, 1),
        "constraints": {
            "gold_used_for_training": False,
            "training_target": "weak_labels -> sector-centre angle (causal)",
            "eval_target": "phase.gold_phase_angle sector (EVAL ONLY, uses future info)",
            "execution_lag": 1,
            "features": [c for c in DEFAULT_PHASE_FEATURES if c in X.columns],
            "alpha": float(alpha),
            "alpha_tuned_once_on_fold0": alpha_tuned,
            "inner_holdout_rows": 252,
            "min_cycle": args.min_cycle,
            "folds": fold_meta,
            "n_rows": int(len(X)),
            "n_oos_rows": int(len(oos_index)),
            "n_oos_rows_scored": int(len(scored_index)),
            "n_oos_legs": n_legs,
            "n_bootstrap": int(args.n_boot),
            "bootstrap_unit": "leg",
            "seed": int(args.seed),
            "gold_sector_vs_gold_labels_agreement": gold_agreement,
            "ensemble_arm": "EnsembleEngine (HMM+GBM) — cheaper than TSFMEnsemble",
            "serving_arms_included": not args.skip_serving_arms,
            "serving_arm_configs": (
                None
                if args.skip_serving_arms
                else {
                    "hmm_shipped": "KostolanyHMM() defaults via TSFMEnsemble.hmm (리듬이)",
                    "gbm_serving": "KostolanyGBM(cycle_smooth=0.0) via TSFMEnsemble.gbm (눈치왕)",
                    "tsfm_shipped": "TSFMEnsemble.predict 0.25/0.55/0.20 (파도꾼)",
                }
            ),
            "enrich_fred": bool(args.enrich_fred),
            "metrics_baselines_available": metrics_baselines is not None,
            # Cross-check: the harness's own battery on the same scored rows. If
            # these disagree with the locally-computed baselines the run is not
            # quotable, because the two lanes are scoring different things.
            "harness_regime_baselines": (
                metrics_baselines(y_true) if metrics_baselines is not None else None
            ),
            "phase_head_kappa_per_fold": kappas,
            "anchor_lambda_per_fold": lambdas,
            "momo_family": momo_names,
            "disclaimer_ko": DISCLAIMER_KO,
        },
        "candidates": results,
        "paired_leg_bootstrap": paired,
        "tripwire": trip,
        "warnings": warnings,
    }

    # Pre-registered family summary: the MEDIAN is the primary comparator, the
    # MAX the adversarial guardrail (G11). Never pick a single best rule.
    fam_rows = [results[m] for m in momo_names if m in results]
    if fam_rows:
        def _fam(metric_get) -> dict[str, float]:
            vals = sorted(metric_get(r) for r in fam_rows)
            return {
                "median": float(np.median(vals)),
                "max": float(max(vals)),
                "min": float(min(vals)),
            }

        payload["momo_floor_summary"] = {
            "side_accuracy": _fam(lambda r: r["factorisation"]["side_accuracy"]),
            "exact6": _fam(lambda r: r["exact6"]),
            "third_given_side": _fam(
                lambda r: r["factorisation"]["third_accuracy_given_side"]
            ),
        }

    out_dir = ROOT / "artifacts" / "experiments"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"phase_head_{args.symbol.replace('^', '')}_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    payload["artifact"] = str(path)
    return payload


def _tune_alpha_once(X: pd.DataFrame, y: pd.Series) -> float:
    """Pick alpha ONCE on the earliest training slice, then freeze it."""
    grid = [0.1, 1.0, 3.0, 10.0, 30.0, 100.0, 300.0]
    cut = int(len(X) * 0.75)
    best, best_score = 10.0, math.inf
    for a in grid:
        head = PhaseHead(alpha=a).fit(X.iloc[:cut], y.iloc[:cut])
        pred = head.predict(X.iloc[cut:])
        target = ((y.iloc[cut:].astype(float) + 0.5) * (math.pi / 3.0)).to_numpy()
        resid = np.abs((target - pred.theta.to_numpy() + math.pi) % (2 * math.pi) - math.pi)
        score = float(np.mean(resid))
        if score < best_score:
            best, best_score = a, score
    return float(best)


def summarize(payload: dict[str, Any]) -> str:
    c = payload["constraints"]
    lines = [
        f"# PhaseHead walk-forward — {payload['symbol']}",
        "",
        f"rows={c['n_rows']}  folds={len(c['folds'])}  OOS scored={c['n_oos_rows_scored']}  "
        f"legs={c['n_oos_legs']}  elapsed={payload['elapsed_sec']}s",
        f"gold_sector vs gold_labels agreement = {c['gold_sector_vs_gold_labels_agreement']:.4f}",
        f"alpha={c['alpha']} (tuned_once={c['alpha_tuned_once_on_fold0']})  kappa/fold="
        + ", ".join(f"{k:.3f}" for k in c["phase_head_kappa_per_fold"]),
        "",
        f"{'candidate':<22}{'exact6':>9}{'adjacent':>10}{'cyc_dist':>10}{'macroF1':>9}"
        f"{'brier':>9}{'ece':>8}",
    ]
    for name, r in payload["candidates"].items():
        lines.append(
            f"{name:<22}{r['exact6']:>9.4f}{r['adjacent']:>10.4f}"
            f"{r['mean_cycle_distance']:>10.4f}{r['macro_f1']:>9.4f}"
            f"{r['brier']:>9.4f}{r['ece']:>8.4f}"
        )
    lines.append("")
    lines.append("95% leg-block bootstrap CIs")
    for name, r in payload["candidates"].items():
        ci = r["ci95_leg_bootstrap"]
        lines.append(
            f"  {name:<22} adjacent [{ci['adjacent'][0]:.4f}, {ci['adjacent'][1]:.4f}]  "
            f"cyc_dist [{ci['mean_cycle_distance'][0]:.4f}, {ci['mean_cycle_distance'][1]:.4f}]  "
            f"exact6 [{ci['exact6'][0]:.4f}, {ci['exact6'][1]:.4f}]"
        )
    lines.append("")
    lines.append("paired leg-block bootstrap: phase_head MINUS other (same resamples)")
    for key, block in payload.get("paired_leg_bootstrap", {}).items():
        other = key.replace("phase_head_minus_", "")
        parts = []
        for metric in ("adjacent", "mean_cycle_distance", "exact6", "brier", "ece"):
            b = block[metric]
            star = "*" if b["excludes_zero"] else " "
            parts.append(f"{metric}={b['delta']:+.4f}[{b['ci95'][0]:+.4f},{b['ci95'][1]:+.4f}]{star}")
        lines.append(f"  vs {other:<22} " + "  ".join(parts))
    lines.append("  (* = 95% CI excludes zero)")
    lines.append("")
    lines.append("factorisation tripwire (side x third|side == exact6)")
    for name, r in payload["candidates"].items():
        f = r["factorisation"]
        lines.append(
            f"  {name:<22} side={f['side_accuracy']:.4f} third|side={f['third_accuracy_given_side']:.4f} "
            f"exact6={f['exact6']:.4f} gap={f['product_gap']:.4f} ok={f['ok']}"
        )
    if payload["warnings"]:
        lines.append("")
        lines.append("WARNINGS")
        for w in payload["warnings"]:
            lines.append(f"  - {w}")
    lines.append("")
    lines.append(DISCLAIMER_KO)
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="PhaseHead walk-forward experiment")
    p.add_argument("--symbol", default="KS11")
    p.add_argument("--start", default="2010-01-01")
    p.add_argument("--n-splits", type=int, default=8)
    p.add_argument("--min-train", type=int, default=1260)
    p.add_argument("--anchor", default=None, help="Forwarded to PurgedWalkForward if supported")
    p.add_argument("--alpha", type=float, default=10.0)
    p.add_argument("--tune-alpha", action="store_true", help="Tune alpha ONCE on fold 0, then freeze")
    p.add_argument("--min-cycle", type=int, default=60)
    p.add_argument("--n-boot", type=int, default=1000)
    p.add_argument("--seed", type=int, default=20260729)
    p.add_argument("--skip-ensemble", action="store_true")
    p.add_argument(
        "--skip-serving-arms",
        action="store_true",
        help="Skip the hmm_shipped/gbm_serving/tsfm_shipped candidates (one TSFMEnsemble fit per fold)",
    )
    p.add_argument(
        "--enrich-fred",
        action="store_true",
        help="Load FRED/Yahoo extras like the serving path does (network required)",
    )
    args = p.parse_args()

    payload = run(args)
    text = summarize(payload)
    md_path = Path(payload["artifact"]).with_suffix(".md")
    md_path.write_text(text, encoding="utf-8")
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    print(f"\nSaved: {payload['artifact']}")
    print(f"Report: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
