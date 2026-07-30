"""SideHead panel evaluation — gates G1..G11 of artifacts/prereg/side_head_v1.json.

12-instrument panel, independent per-market fits (NO pooled coefficients),
two-level paired cluster bootstrap (outer=instrument, inner=leg, shared draw
matrix), Bonferroni z=3.20 for every primary hypothesis. Gold labels are used
for SCORING ONLY.

Usage:
  .\\.venv\\Scripts\\python.exe scripts\\run_side_panel.py
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from kostolany.connectors import load_market  # noqa: E402
from kostolany.engine import prepare_xy  # noqa: E402
from kostolany.labels_pit import clock_terciles, clock_third, pit_state  # noqa: E402
from kostolany.phase import PhaseHead, gold_leg_segments, gold_phase_sectors  # noqa: E402
from kostolany.side import SideHead  # noqa: E402
from run_phase_experiment import (  # noqa: E402
    MOMO_MA_WINDOWS,
    MOMO_RET_HORIZONS,
    _onehot_proba,
    build_folds,
    factorisation,
    score_arrays,
)

PREREG = json.loads((ROOT / "artifacts" / "prereg" / "side_head_v1.json").read_text("utf-8"))
PANEL: list[str] = list(PREREG["panel"])
UNIFORM_BRIER = 5.0 / 36.0
N_BOOT = 2000
SEED = 20260729
Z_BONF = 3.20

MOMO_NAMES = [f"momo_ma{w}" for w in MOMO_MA_WINDOWS] + [
    f"momo_ret{h}" for h in MOMO_RET_HORIZONS
]
CANDS = ["side_head", "phase_head", *MOMO_NAMES]


def run_market(symbol: str) -> dict[str, Any] | None:
    t0 = time.time()
    try:
        market = load_market(symbol, start="2010-01-01", enrich_fred=True)
        X, y_weak, _g, prices = prepare_xy(market)
    except Exception as exc:  # noqa: BLE001
        print(f"  [{symbol}] load failed: {exc}", flush=True)
        return None
    valid = (
        X.dropna().index.intersection(y_weak.dropna().index).intersection(prices.dropna().index)
    )
    X, y_weak = X.loc[valid], y_weak.loc[valid].astype(int)
    prices = prices.loc[valid].astype(float)
    if len(X) < 1260 + 300:
        print(f"  [{symbol}] too short: {len(X)}", flush=True)
        return None

    gold = gold_phase_sectors(prices, min_cycle=60).reindex(valid)
    segments = gold_leg_segments(prices, min_cycle=60).reindex(valid)
    clock = pit_state(prices)
    momo_up = {}
    for w in MOMO_MA_WINDOWS:
        momo_up[f"momo_ma{w}"] = prices > prices.rolling(w, min_periods=w // 2).mean()
    for h in MOMO_RET_HORIZONS:
        momo_up[f"momo_ret{h}"] = prices.pct_change(h) > 0

    folds = build_folds(len(X), 8, 1260, "end")
    preds: dict[str, list[pd.Series]] = {c: [] for c in CANDS}
    probas: dict[str, list[pd.DataFrame]] = {c: [] for c in CANDS}
    cols = [f"p{i}" for i in range(6)]
    lambdas: list[float] = []

    for fold in folds:
        Xtr, ytr = X.iloc[fold.train_idx], y_weak.iloc[fold.train_idx]
        Xte = X.iloc[fold.test_idx]
        idx = Xte.index

        head = SideHead()
        r, p = head.fit_predict(Xtr, prices, ytr, Xte)
        lambdas.append(float(head.lambda_))
        preds["side_head"].append(r.reindex(idx))
        probas["side_head"].append(p.reindex(idx)[cols])

        ph = PhaseHead(alpha=10.0)
        r, p = ph.fit_predict(Xtr, ytr, Xte)
        preds["phase_head"].append(r.reindex(idx))
        probas["phase_head"].append(p.reindex(idx)[cols])

        clk_tr = clock.reindex(Xtr.index)
        cuts = clock_terciles(clk_tr.loc[clk_tr["side"] != 0, "k"])
        thirds = clock_third(clock.reindex(idx)["k"], cuts)
        for mname in MOMO_NAMES:
            up = momo_up[mname].reindex(idx).fillna(True).to_numpy()
            mclass = np.where(up, thirds, 3 + thirds).astype(int)
            preds[mname].append(pd.Series(mclass, index=idx))
            probas[mname].append(pd.DataFrame(_onehot_proba(mclass), index=idx, columns=cols))

    oos_index = pd.Index(np.concatenate([s.index.to_numpy() for s in preds["side_head"]]))
    seg = segments.reindex(oos_index)
    scored = oos_index[(seg["leg_id"] >= 0).to_numpy()]
    y_true = gold.reindex(scored).to_numpy(dtype=int)
    leg_of_row = seg.loc[scored, "leg_id"].to_numpy(dtype=int)

    out: dict[str, Any] = {
        "symbol": symbol,
        "n_oos": int(len(scored)),
        "n_legs": int(len(np.unique(leg_of_row))),
        "lambda_per_fold": lambdas,
        "candidates": {},
        # raw arrays for the panel-level shared bootstrap
        "_y": y_true,
        "_legs": leg_of_row,
        "_pred": {},
        "_proba": {},
    }
    for name in CANDS:
        yp = pd.concat(preds[name]).reindex(scored).to_numpy(dtype=int)
        P = pd.concat(probas[name]).reindex(scored).to_numpy(dtype=float)
        P = P / np.clip(P.sum(axis=1, keepdims=True), 1e-12, None)
        s = score_arrays(y_true, yp, P)
        f = factorisation(y_true, yp)
        out["candidates"][name] = {**s, "side_accuracy": f["side_accuracy"],
                                   "third_given_side": f["third_accuracy_given_side"]}
        out["_pred"][name] = yp
        out["_proba"][name] = P
    print(
        f"  [{symbol}] n={len(scored)} legs={out['n_legs']} "
        f"side_head={out['candidates']['side_head']['side_accuracy']:.4f} "
        f"({time.time() - t0:.0f}s)",
        flush=True,
    )
    return out


def side_hits(y: np.ndarray, pred: np.ndarray) -> np.ndarray:
    return ((y >= 3) == (pred >= 3)).astype(float)


def brier_rows(y: np.ndarray, P: np.ndarray) -> np.ndarray:
    onehot = np.eye(6)[y]
    return np.mean((P - onehot) ** 2, axis=1)


def main() -> int:
    t0 = time.time()
    print(f"[panel] {len(PANEL)} instruments: {PANEL}", flush=True)
    markets = [m for m in (run_market(s) for s in PANEL) if m is not None]
    if len(markets) < 8:
        print(f"FATAL: only {len(markets)} markets scored", flush=True)
        return 1

    # ---- per-replicate shared two-level bootstrap --------------------------
    rng = np.random.default_rng(SEED)
    n_m = len(markets)
    # Precompute per-market per-candidate row statistics
    stats = []
    for m in markets:
        leg_ids = np.unique(m["_legs"])
        leg_pos = {int(l): np.flatnonzero(m["_legs"] == l) for l in leg_ids}
        stats.append(
            {
                "legs": leg_ids,
                "leg_pos": leg_pos,
                "side": {c: side_hits(m["_y"], m["_pred"][c]) for c in CANDS},
                "brier": {c: brier_rows(m["_y"], m["_proba"][c]) for c in CANDS},
            }
        )

    boot = {
        "side_median_panel": [],           # G1 input (side_head)
        "d_side_vs_momo_median": [],       # G2
        "d_side_vs_phase": [],             # G3
        "d_brier_vs_uniform": [],          # G4/G5
        "d_side_vs_momo_max": [],          # G11
    }
    for _ in range(N_BOOT):
        m_draw = rng.integers(0, n_m, size=n_m)  # outer: instruments
        acc: dict[str, list[float]] = {c: [] for c in CANDS}
        brs: list[float] = []
        for mi in m_draw:
            st = stats[mi]
            legs = st["legs"]
            draw = rng.integers(0, len(legs), size=len(legs))  # inner: legs
            sel = np.concatenate([st["leg_pos"][int(legs[d])] for d in draw])
            for c in CANDS:
                acc[c].append(float(np.mean(st["side"][c][sel])))
            brs.append(float(np.mean(st["brier"]["side_head"][sel])))
        med = {c: float(np.median(acc[c])) for c in CANDS}
        momo_meds = [med[c] for c in MOMO_NAMES]
        boot["side_median_panel"].append(med["side_head"])
        boot["d_side_vs_momo_median"].append(med["side_head"] - float(np.median(momo_meds)))
        boot["d_side_vs_momo_max"].append(med["side_head"] - float(np.max(momo_meds)))
        boot["d_side_vs_phase"].append(med["side_head"] - med["phase_head"])
        boot["d_brier_vs_uniform"].append(float(np.median(brs)) - UNIFORM_BRIER)

    def _summ(v: list[float]) -> dict[str, float]:
        a = np.asarray(v)
        return {
            "point": float(np.mean(a)),
            "sd": float(np.std(a, ddof=1)),
            "p2.5": float(np.percentile(a, 2.5)),
            "p97.5": float(np.percentile(a, 97.5)),
            "z320_lo": float(np.mean(a) - Z_BONF * np.std(a, ddof=1)),
            "z320_hi": float(np.mean(a) + Z_BONF * np.std(a, ddof=1)),
        }

    B = {k: _summ(v) for k, v in boot.items()}

    # ---- point panel medians ----------------------------------------------
    def _panel_median(metric, cand="side_head"):
        return float(np.median([m["candidates"][cand][metric] for m in markets]))

    side_med = _panel_median("side_accuracy")
    momo_family_med = float(
        np.median([np.median([m["candidates"][c]["side_accuracy"] for c in MOMO_NAMES]) for m in markets])
    )
    ece_list = [m["candidates"]["side_head"]["ece"] for m in markets]
    third_med = _panel_median("third_given_side")

    gates = {
        "G1_side_panel_median>=0.640": bool(side_med >= 0.640),
        "G2_noninferior_vs_momo_median": bool(
            B["d_side_vs_momo_median"]["point"] >= 0.0
            and B["d_side_vs_momo_median"]["z320_lo"] > -0.010
        ),
        "G3_vs_phase_head>=+0.050_CI_excl0": bool(
            B["d_side_vs_phase"]["point"] >= 0.050 and B["d_side_vs_phase"]["z320_lo"] > 0.0
        ),
        "G4_brier_noninferior_uniform": bool(B["d_brier_vs_uniform"]["z320_hi"] < 0.0010),
        "G5_brier_superior_uniform": bool(
            _panel_median("brier") <= 0.1369 and B["d_brier_vs_uniform"]["z320_hi"] < 0.0
        ),
        "G6_ece_median<=0.060_and_10of12<=0.10": bool(
            float(np.median(ece_list)) <= 0.060
            and sum(e <= 0.10 for e in ece_list) >= min(10, len(markets) - 2)
        ),
        "G7_exact6_median>=0.235": bool(_panel_median("exact6") >= 0.235),
        "G8_adjacent_median>=0.620": bool(_panel_median("adjacent") >= 0.620),
        "G9_cyc_dist_median<=1.240": bool(_panel_median("mean_cycle_distance") <= 1.240),
        "G10_clock_third_in_band": bool(0.355 <= third_med <= 0.385),
        "G11_not_worse_than_momo_max_by_0.010": bool(
            B["d_side_vs_momo_max"]["point"] >= -0.010
        ),
    }
    # G2 identifiability disclosure (pre-registered): a point in (0, 0.015) is
    # NOT IDENTIFIED, never a win.
    g2_point = B["d_side_vs_momo_median"]["point"]
    g2_status = (
        "NOT IDENTIFIED (below corrected MDE 0.050)" if 0.0 < g2_point < 0.015 else "as measured"
    )

    payload = {
        "kind": "side_panel_v1",
        "prereg": "artifacts/prereg/side_head_v1.json",
        "prereg_git_sha": "0f6c12e",
        "asof": datetime.now(timezone.utc).isoformat(),
        "panel_scored": [m["symbol"] for m in markets],
        "per_market": {
            m["symbol"]: {
                "n_oos": m["n_oos"],
                "n_legs": m["n_legs"],
                "lambda_per_fold": m["lambda_per_fold"],
                "candidates": m["candidates"],
            }
            for m in markets
        },
        "panel_medians": {
            "side_head_side": side_med,
            "momo_family_side_median": momo_family_med,
            "phase_head_side": _panel_median("side_accuracy", "phase_head"),
            "side_head_brier": _panel_median("brier"),
            "side_head_ece_median": float(np.median(ece_list)),
            "side_head_exact6": _panel_median("exact6"),
            "side_head_adjacent": _panel_median("adjacent"),
            "side_head_cyc_dist": _panel_median("mean_cycle_distance"),
            "clock_third_given_side": third_med,
        },
        "bootstrap": B,
        "gates": gates,
        "g2_identifiability": g2_status,
        "kill_conditions_fired": {
            "K1": not gates["G3_vs_phase_head>=+0.050_CI_excl0"],
            "K2": not gates["G2_noninferior_vs_momo_median"],
            "K3": not gates["G4_brier_noninferior_uniform"],
            "K8": bool(side_med < 0.600),
        },
        "elapsed_sec": round(time.time() - t0, 1),
        "disclaimer_ko": "본 정보는 교육·연구 목적이며 투자 권유·자문이 아닙니다.",
    }

    out_dir = ROOT / "artifacts" / "experiments"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = out_dir / f"side_panel_{stamp}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n==== PANEL VERDICT ====")
    for k, v in gates.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print(f"  G2 point {g2_point:+.4f} -> {g2_status}")
    print(f"  side medians: side_head {side_med:.4f} | momo family {momo_family_med:.4f} | "
          f"phase_head {payload['panel_medians']['phase_head_side']:.4f}")
    for k, v in payload["kill_conditions_fired"].items():
        if v:
            print(f"  !! KILL {k} FIRED")
    print(f"Saved: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
