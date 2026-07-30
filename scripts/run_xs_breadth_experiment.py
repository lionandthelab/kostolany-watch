"""P-XS-1: does real cross-sectional breadth move the side factor?

Pre-registration: docs/XS_BREADTH_PREREG_2026-07-30.md (written BEFORE this
script was first run; thresholds are frozen there).

Task: binary side classification (up-leg vs down-leg) on ^GSPC.
  train target = weak side (causal)   ·   scoring target = gold side (EVAL ONLY)
Arms:
  base          23-feature model_matrix (what serving models see)
  causal        base + 10 cross-sectional ratio-breadth columns
  shuffle       breadth block time-shuffled  -> gain must vanish (plumbing canary)
  leak          breadth block shifted 1 day into the future -> must beat causal
                (if causal beats deliberate lookahead, the "gain" is noise fit)

Usage:
  .\\.venv\\Scripts\\python.exe scripts\\run_xs_breadth_experiment.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kostolany.connectors import load_market  # noqa: E402
from kostolany.connectors.cache import read_cache, write_cache  # noqa: E402
from kostolany.data import fetch_yahoo  # noqa: E402
from kostolany.engine import prepare_xy  # noqa: E402
from kostolany.harness.cv import PurgedWalkForward  # noqa: E402
from kostolany.phase import gold_leg_segments, gold_phase_sectors  # noqa: E402

RATIOS: list[tuple[str, str, str]] = [
    ("rsp_spy", "RSP", "SPY"),
    ("iwm_spy", "IWM", "SPY"),
    ("xly_xlp", "XLY", "XLP"),
    ("hyg_lqd", "HYG", "LQD"),
    ("vix_ts", "^VIX", "^VIX3M"),
]
SYMBOL = "^GSPC"
START = "2010-01-01"
N_SPLITS = 8
MIN_TRAIN = 1260
N_BOOT = 1000
SEED = 20260730


def _causal_z(s: pd.Series, window: int = 120) -> pd.Series:
    med = s.rolling(window, min_periods=40).median()
    mad = (s - med).abs().rolling(window, min_periods=40).median()
    scale = (1.4826 * mad).replace(0, np.nan)
    return ((s - med) / scale).clip(-8, 8)


def _close(symbol: str) -> pd.Series:
    key = f"xs_{symbol.replace('^', '').lower()}_{START}"
    cached = read_cache(key, max_age_hours=24.0)
    if cached is not None and not cached.empty:
        return cached["close"].astype(float)
    md = fetch_yahoo(symbol, start=START)
    write_cache(key, md.ohlcv)
    return md.ohlcv["close"].astype(float)


def build_breadth_block(index: pd.DatetimeIndex) -> pd.DataFrame:
    cols: dict[str, pd.Series] = {}
    for name, num, den in RATIOS:
        a, b = _close(num), _close(den)
        ratio = np.log(a).sub(np.log(b), fill_value=np.nan).dropna()
        # Same-calendar US closes; align to the target index without lookahead.
        ratio = ratio.reindex(index).ffill(limit=3)
        cols[f"{name}_lvl_z"] = _causal_z(ratio)
        cols[f"{name}_mom_z"] = _causal_z(ratio.diff(20))
    return pd.DataFrame(cols, index=index)


def _fit_predict_side(Xtr: pd.DataFrame, ytr: np.ndarray, Xte: pd.DataFrame) -> np.ndarray:
    import lightgbm as lgb

    clf = lgb.LGBMClassifier(
        n_estimators=400,
        learning_rate=0.03,
        num_leaves=31,
        min_child_samples=35,
        subsample=0.82,
        subsample_freq=1,
        colsample_bytree=0.78,
        reg_alpha=0.15,
        reg_lambda=2.5,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
        verbose=-1,
    )
    clf.fit(Xtr, ytr)
    return clf.predict_proba(Xte)[:, 1]


def _auc(y: np.ndarray, p: np.ndarray) -> float:
    from sklearn.metrics import roc_auc_score

    if len(np.unique(y)) < 2:
        return float("nan")
    return float(roc_auc_score(y, p))


def main() -> int:
    t0 = time.time()
    market = load_market(SYMBOL, start=START, enrich_fred=True)
    X, y_weak, _gold, prices = prepare_xy(market)

    breadth = build_breadth_block(X.index)
    valid = (
        X.dropna()
        .index.intersection(y_weak.dropna().index)
        .intersection(breadth.dropna().index)
        .intersection(prices.dropna().index)
    )
    X = X.loc[valid]
    breadth = breadth.loc[valid]
    y_side_weak = (y_weak.loc[valid].astype(int) >= 3).astype(int)  # causal train target
    prices = prices.loc[valid].astype(float)

    # EVAL ONLY targets
    gold_sector = gold_phase_sectors(prices, min_cycle=60).reindex(valid)
    segments = gold_leg_segments(prices, min_cycle=60).reindex(valid)
    y_side_gold = (gold_sector >= 3).astype(int)

    # Canary variants of the block (built once; deterministic seed)
    rng = np.random.default_rng(SEED)
    perm = rng.permutation(len(breadth))
    breadth_shuffled = pd.DataFrame(
        breadth.to_numpy()[perm], index=breadth.index, columns=breadth.columns
    )
    breadth_leak = breadth.shift(-1)  # tomorrow's block today — deliberate lookahead

    arms = {
        "base": X,
        "causal": X.join(breadth),
        "shuffle": X.join(breadth_shuffled),
        "leak": X.join(breadth_leak),
    }

    cv = PurgedWalkForward(
        n_splits=N_SPLITS,
        min_train_size=MIN_TRAIN,
        test_size=max(21, (len(X) - MIN_TRAIN) // N_SPLITS),
        purge_horizon=5,
        embargo=5,
        expanding=True,
        anchor="end",
    )
    folds = list(cv.split(np.zeros((len(X), 1))))
    print(f"[setup] rows={len(X)} folds={len(folds)} window={valid[0].date()}..{valid[-1].date()}")

    proba: dict[str, list[pd.Series]] = {k: [] for k in arms}
    for fold in folds:
        tr, te = fold.train_idx, fold.test_idx
        idx = X.index[te]
        ytr = y_side_weak.iloc[tr].to_numpy()
        for name, Xa in arms.items():
            # leak arm: the last training row uses t+1 data that crosses into
            # the test window — drop it from train so only the FEATURE timing
            # differs, not the sample count. (dropna handles it uniformly.)
            Xtr = Xa.iloc[tr].dropna()
            p = _fit_predict_side(Xtr, y_side_weak.reindex(Xtr.index).to_numpy(), Xa.iloc[te].ffill().bfill())
            proba[name].append(pd.Series(p, index=idx))
        print(f"[fold {fold.fold_id}] done train={len(tr)} test={len(te)}", flush=True)

    oos_index = pd.Index(np.concatenate([s.index.to_numpy() for s in proba["base"]]))
    seg = segments.reindex(oos_index)
    scored = oos_index[(seg["leg_id"] >= 0).to_numpy()]
    y_true = y_side_gold.reindex(scored).to_numpy()
    leg_of_row = seg.loc[scored, "leg_id"].to_numpy(dtype=int)
    leg_ids = np.unique(leg_of_row)
    stacked = {k: pd.concat(v).reindex(scored).to_numpy() for k, v in proba.items()}

    point = {k: {"auc": _auc(y_true, p), "acc": float(np.mean((p > 0.5) == y_true))} for k, p in stacked.items()}

    # Paired leg-block bootstrap on the SAME resamples
    leg_pos = {int(l): np.flatnonzero(leg_of_row == l) for l in leg_ids}
    draws = np.random.default_rng(SEED).integers(0, len(leg_ids), size=(N_BOOT, len(leg_ids)))
    deltas = {f"{k}_minus_base": [] for k in ("causal", "shuffle", "leak")}
    causal_minus_leak: list[float] = []
    for b in range(N_BOOT):
        sel = np.concatenate([leg_pos[int(leg_ids[d])] for d in draws[b]])
        yb = y_true[sel]
        if len(np.unique(yb)) < 2:
            continue
        aucs = {k: _auc(yb, p[sel]) for k, p in stacked.items()}
        for k in ("causal", "shuffle", "leak"):
            deltas[f"{k}_minus_base"].append(aucs[k] - aucs["base"])
        causal_minus_leak.append(aucs["causal"] - aucs["leak"])

    def _ci(v: list[float]) -> dict[str, float]:
        a = np.asarray(v, dtype=float)
        return {
            "delta_mean": float(np.mean(a)),
            "ci_lo": float(np.percentile(a, 2.5)),
            "ci_hi": float(np.percentile(a, 97.5)),
            "excludes_zero": bool(np.percentile(a, 2.5) > 0 or np.percentile(a, 97.5) < 0),
        }

    boot = {k: _ci(v) for k, v in deltas.items()}
    boot["causal_minus_leak"] = _ci(causal_minus_leak)

    d_causal = point["causal"]["auc"] - point["base"]["auc"]
    d_shuffle = point["shuffle"]["auc"] - point["base"]["auc"]
    d_leak = point["leak"]["auc"] - point["base"]["auc"]
    verdict = {
        "delta_causal_auc": d_causal,
        "threshold": 0.030,
        "meets_threshold": bool(d_causal >= 0.030),
        "ci_excludes_zero": boot["causal_minus_base"]["excludes_zero"],
        "shuffle_canary_pass": bool(
            not (boot["shuffle_minus_base"]["ci_lo"] <= d_causal <= boot["shuffle_minus_base"]["ci_hi"])
        ),
        "alignment_probe_pass": bool(d_leak >= d_causal - 0.01),
        "adopt": False,
    }
    verdict["adopt"] = bool(
        verdict["meets_threshold"]
        and verdict["ci_excludes_zero"]
        and verdict["shuffle_canary_pass"]
        and verdict["alignment_probe_pass"]
    )

    payload = {
        "kind": "xs_breadth_side_v1",
        "preregistration": "docs/XS_BREADTH_PREREG_2026-07-30.md",
        "asof": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "window": f"{valid[0].date()}..{valid[-1].date()}",
        "n_rows": int(len(X)),
        "n_oos_scored": int(len(scored)),
        "n_legs": int(len(leg_ids)),
        "block_columns": list(breadth.columns),
        "point": point,
        "paired_leg_bootstrap": boot,
        "verdict": verdict,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    out = ROOT / "artifacts" / "experiments"
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out / f"xs_breadth_GSPC_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({"point": point, "verdict": verdict}, indent=2))
    print(f"Saved: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
