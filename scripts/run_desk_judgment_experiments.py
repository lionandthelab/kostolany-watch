#!/usr/bin/env python3
"""Execute the pre-registered desk-judgment experiments.

Prereg: docs/DESK_JUDGMENT_PREREG_2026-08-07.md — read it first. Everything the
runner is allowed to decide is frozen there; this file executes, it does not
choose. In particular the free parameters below are checked against the document
at start-up and the run refuses to proceed on any mismatch (prereg §12), which
is the only mechanical defence against KJ7 (post-hoc parameter movement).

Scope is the owner-approved D5 set: P-DJ-1, P-COND-1, P-FLIP-1, P-RUN-1.
P-ANALOG-1 stays on hold and is not implemented here — writing the code would
be the first step toward running it.

Usage:
    .venv/Scripts/python.exe scripts/run_desk_judgment_experiments.py [--only P-DJ-1]
"""

from __future__ import annotations

import argparse
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

from kostolany.api import WATCH_MARKETS  # noqa: E402
from kostolany.connectors import load_market  # noqa: E402
from kostolany.engine import prepare_xy  # noqa: E402
from kostolany.harness.cv import PurgedWalkForward  # noqa: E402
from kostolany.momo import MA_WINDOWS, RET_HORIZONS, RULE_IDS, MomoFloorHead  # noqa: E402
from kostolany.phase import gold_leg_segments  # noqa: E402

PREREG_DOC = "docs/DESK_JUDGMENT_PREREG_2026-08-07.md"
OUT_DIR = ROOT / "artifacts" / "experiments"

# ---------------------------------------------------------------- protocol
# Prereg §1. Inherited, not chosen — every value here has a cited origin and
# none of them is a free parameter.
CV_KWARGS = dict(
    n_splits=8, min_train_size=1260, purge_horizon=5, embargo=5, anchor="end"
)
MIN_CYCLE = 60
N_BOOT = 2000
SEED = 20260729
ALPHA = 0.05
Z = 2.5758  # Bonferroni α/5 over the 5-experiment family, two-sided
CONFIRM_LAG = 10  # labels_pit.confirm_lag — inherited, prereg §3.1

# Prereg §11. The runner asserts these against the document before doing
# anything; a silent drift between code and prereg is the failure this guards.
FREE_PARAMS: dict[str, dict[str, Any]] = {
    "P-DJ-1": {"J1": "{4} vs {1,2,3}", "J2": 0.030, "J3": 12},
    "P-FLIP-1": {"J4": "tercile", "J5": 0.10, "J6": 40},
    "P-RUN-1": {"J7": [20, 60], "J8": 0.05, "J9": 12, "J10": 0.50},
    "P-COND-1": {"J17": [10, 20, 60], "J18": 12, "J19": 0.10, "J20": 6},
}

VOTE_TIERS = {8: "unanimous", 7: "strong", 6: "lean"}


def _prereg_text() -> str:
    return (ROOT / PREREG_DOC).read_text(encoding="utf-8")


def assert_free_params_match_prereg() -> None:
    """Refuse to run if code and document disagree (prereg §12).

    Deliberately crude — it greps for each value's literal appearance in the
    document rather than parsing it. A precise parser would be one more thing
    that can be quietly adjusted; a grep fails loudly when a number moves.
    """
    doc = _prereg_text()
    problems: list[str] = []
    checks = [
        ("J2", "0.030"), ("J3", "`12`"), ("J5", "0.10"), ("J6", "40"),
        ("J7", "`20`, `60`"), ("J8", "`0.05`"), ("J10", "`0.50`"),
        ("J17", "{10, 20, 60}"), ("J19", "`0.10`"), ("J20", "`6`"),
    ]
    for jid, literal in checks:
        if literal not in doc:
            problems.append(f"{jid}: {literal!r} not found in {PREREG_DOC}")
    for name, value in (
        ("n_boot", "2000"), ("seed", "20260729"), ("z", "2.5758"),
        ("cv", "n_splits=8, min_train=1260"),
    ):
        if value not in doc.replace(",", ",").replace("`", ""):
            problems.append(f"protocol {name}={value} not found in prereg")
    if RET_HORIZONS != (10, 20, 60):
        problems.append(f"momo.RET_HORIZONS drifted: {RET_HORIZONS}")
    if problems:
        raise SystemExit(
            "REFUSING TO RUN — code and pre-registration disagree:\n  "
            + "\n  ".join(problems)
        )


# ------------------------------------------------------------------ market


def _rule_boundaries(px: pd.Series) -> pd.DataFrame:
    """Per-bar close that puts each rule exactly on its boundary.

    The vectorised twin of `momo.rule_flip_levels`, which only ever computes the
    last bar. Identity, not approximation — `test_flip_identity` below asserts
    the two agree on the final bar of every series the run touches.

    `min_periods` is the full window on purpose: `momo.rule_votes` allows a
    partial mean early in a series, and there the closed form would describe a
    boundary the served vote never used. Those bars come back NaN and are
    dropped rather than scored against the wrong number.
    """
    out: dict[str, pd.Series] = {}
    for w in MA_WINDOWS:
        out[f"ma{w}"] = px.shift(1).rolling(w - 1, min_periods=w - 1).mean()
    for h in RET_HORIZONS:
        out[f"ret{h}"] = px.shift(h)
    return pd.DataFrame(out)[list(RULE_IDS)]


def _rule_votes_full(px: pd.Series) -> pd.DataFrame:
    """`momo.rule_votes` with full windows only — see `_rule_boundaries`."""
    out: dict[str, pd.Series] = {}
    for w in MA_WINDOWS:
        out[f"ma{w}"] = px > px.rolling(w, min_periods=w).mean()
    for h in RET_HORIZONS:
        out[f"ret{h}"] = px.pct_change(h) > 0
    return pd.DataFrame(out)[list(RULE_IDS)]


def _side_flip_move_pct(px: pd.Series) -> pd.Series:
    """Per-bar |move| that would take the majority across, as a signed fraction.

    Mirrors `engine._flip_block`: among the rules voting with the called side,
    sorted by |distance|, the `need`-th is the one whose flip moves the majority.
    """
    votes = _rule_votes_full(px)
    bounds = _rule_boundaries(px)
    move = bounds.div(px, axis=0) - 1.0

    up_count = votes.sum(axis=1)
    side_up = up_count >= 4
    need = np.where(side_up, up_count - 3, 4 - up_count).astype(float)

    v = votes.to_numpy()
    m = move.to_numpy()
    su = side_up.to_numpy()
    nd = need  # already an ndarray out of np.where
    out = np.full(len(px), np.nan)
    for i in range(len(px)):
        if not np.isfinite(m[i]).all() or np.isnan(nd[i]):
            continue
        called = v[i] == su[i]
        cand = np.sort(np.abs(m[i][called]))
        k = int(nd[i])
        if 1 <= k <= len(cand):
            out[i] = cand[k - 1]
    return pd.Series(out, index=px.index, name="side_flip_move_pct")


def build_market(symbol: str) -> dict[str, Any] | None:
    """Walk-forward OOS calls for all four served heads plus the eval columns."""
    t0 = time.time()
    try:
        market = load_market(symbol)
        X, y_weak, y_gold, close = prepare_xy(market)
    except Exception as exc:  # noqa: BLE001
        print(f"  [{symbol}] LOAD FAILED: {exc}", flush=True)
        return None

    X = X.dropna()
    common = X.index.intersection(y_weak.dropna().index).intersection(y_gold.index)
    X, y_weak = X.loc[common], y_weak.loc[common]
    close_all = close.astype(float).sort_index()

    seg = gold_leg_segments(close_all, min_cycle=MIN_CYCLE)
    gold_side = pd.Series(seg["side"].to_numpy(), index=close_all.index)
    leg_id = pd.Series(seg["leg_id"].to_numpy(), index=close_all.index)

    cv = PurgedWalkForward(**CV_KWARGS)
    folds = list(cv.split(X))
    if not folds:
        print(f"  [{symbol}] no folds", flush=True)
        return None

    from kostolany.models import KostolanyGBM, KostolanyHMM
    from kostolany.tsfm import TSFMEnsemble

    rows: list[pd.DataFrame] = []
    for fi, fold in enumerate(folds):
        tr, te = X.index[fold.train_idx], X.index[fold.test_idx]
        if len(tr) < 100 or len(te) == 0:
            continue
        Xtr, ytr, Xte = X.loc[tr], y_weak.loc[tr], X.loc[te]

        calls: dict[str, pd.Series] = {}
        try:
            # Constructed exactly as `engine._make_model` does for serving —
            # this measures the shipped heads, not tuned cousins of them.
            for name, head in (
                ("hmm", KostolanyHMM()),
                ("gbm", KostolanyGBM()),
                ("tsfm", TSFMEnsemble()),
            ):
                reg, _ = head.fit_predict(Xtr, ytr, Xte)
                calls[name] = reg.reindex(te)
            # momo consumes prices; give it the causal prefix ending at the test
            # block so nothing after the scored bar is visible.
            px_prefix = close_all.loc[:te[-1]]
            momo = MomoFloorHead(min_cycle=MIN_CYCLE)
            reg_m, _ = momo.fit_predict(close_all.loc[:tr[-1]], px_prefix)
            calls["momo"] = reg_m.reindex(te)
        except Exception as exc:  # noqa: BLE001
            # K-DJ-3: a failed fold poisons the whole run. Selectively dropping
            # it and re-running is exactly the post-hoc selection the prereg
            # forbids, so this is recorded and surfaced, never swallowed.
            print(f"  [{symbol}] fold {fi} FAILED: {exc}", flush=True)
            return {"symbol": symbol, "fold_failure": f"fold {fi}: {exc}"}

        df = pd.DataFrame({f"{k}_regime": v for k, v in calls.items()}, index=te)
        df["fold"] = fi
        rows.append(df)

    oos = pd.concat(rows).sort_index()
    oos = oos.dropna(subset=[f"{k}_regime" for k in ("momo", "hmm", "gbm", "tsfm")])

    for k in ("momo", "hmm", "gbm", "tsfm"):
        # side is read off the regime id (<3 == up), never recomputed from the
        # probability vector — prereg §2.2.
        oos[f"{k}_side"] = np.where(oos[f"{k}_regime"].to_numpy() < 3, 1, -1)

    votes = _rule_votes_full(close_all).reindex(oos.index)
    oos["votes_up"] = votes.sum(axis=1)
    maj = np.maximum(oos["votes_up"], 8 - oos["votes_up"]).astype(int)
    oos["tier"] = [VOTE_TIERS.get(int(m), "mixed") for m in maj]
    oos["flip_move"] = _side_flip_move_pct(close_all).reindex(oos.index).abs()
    oos["gold_side"] = gold_side.reindex(oos.index)
    oos["leg_id"] = leg_id.reindex(oos.index)

    # momo side run-length: bars the current side call has stood, counted on the
    # full causal series so a run that starts before the OOS window is not
    # truncated to the window's own start.
    momo_side_full = pd.Series(
        np.where(
            MomoFloorHead(min_cycle=MIN_CYCLE)
            .fit(close_all)
            .predict(close_all)[0]
            .to_numpy()
            < 3,
            1,
            -1,
        ),
        index=close_all.index,
    )
    grp = (momo_side_full != momo_side_full.shift()).cumsum()
    oos["run_len"] = momo_side_full.groupby(grp).cumcount().add(1).reindex(oos.index)

    # Forward gold side at each frozen horizon (P-COND-1). No return column is
    # created anywhere in this file — prereg §6.1 / K-CD-2.
    for h in RET_HORIZONS:
        oos[f"gold_side_fwd{h}"] = gold_side.shift(-h).reindex(oos.index)

    # Realised side transition within CONFIRM_LAG bars (P-FLIP-1).
    fwd = pd.concat(
        [gold_side.shift(-k) for k in range(1, CONFIRM_LAG + 1)], axis=1
    ).reindex(oos.index)
    oos["flips_within_lag"] = (fwd.ne(oos["gold_side"], axis=0)).any(axis=1).astype(float)
    oos.loc[fwd.isna().any(axis=1), "flips_within_lag"] = np.nan

    oos = oos.dropna(subset=["gold_side", "leg_id"])
    oos["momo_hit"] = (oos["momo_side"] == oos["gold_side"]).astype(float)
    oos["n_side_agree"] = sum(
        (oos[f"{k}_side"] == oos["momo_side"]).astype(int)
        for k in ("momo", "hmm", "gbm", "tsfm")
    )

    print(
        f"  [{symbol}] OOS n={len(oos)} legs={oos['leg_id'].nunique()} "
        f"folds={len(rows)} ({time.time() - t0:.0f}s)",
        flush=True,
    )
    return {"symbol": symbol, "oos": oos}


def test_flip_identity(symbol: str, close: pd.Series) -> bool:
    """The vectorised boundary must equal `momo.rule_flip_levels` on the last bar."""
    ref = MomoFloorHead(min_cycle=MIN_CYCLE).rule_flip_levels(close)
    if ref.empty:
        return False
    mine = _rule_boundaries(close).iloc[-1]
    ok = bool(np.allclose(ref.to_numpy(float), mine.to_numpy(float), rtol=1e-12))
    print(f"  [{symbol}] flip-boundary identity vs momo.rule_flip_levels: {ok}", flush=True)
    return ok


# --------------------------------------------------------------- bootstrap


class LegBootstrap:
    """Stratified leg bootstrap with a resample matrix shared by every arm.

    Prereg §1: outer level is the market (fixed effect, per-market leg counts
    preserved), inner level is the leg. Drawing fresh legs per arm would break
    the pairing and inflate every difference's CI, so the draws are computed
    once here and reused by all callers.
    """

    def __init__(self, frames: dict[str, pd.DataFrame], seed: int = SEED) -> None:
        self.symbols = sorted(frames)
        self.frames = frames
        rng = np.random.default_rng(seed)
        self.draws: list[dict[str, np.ndarray]] = []
        leg_pos = {
            s: {int(leg): np.flatnonzero(frames[s]["leg_id"].to_numpy() == leg)
                for leg in np.unique(frames[s]["leg_id"].to_numpy())}
            for s in self.symbols
        }
        self._leg_pos = leg_pos
        for _ in range(N_BOOT):
            draw = {}
            for s in self.symbols:
                legs = np.array(sorted(leg_pos[s]))
                pick = rng.integers(0, len(legs), size=len(legs))
                draw[s] = np.concatenate([leg_pos[s][int(legs[p])] for p in pick])
            self.draws.append(draw)

    def pooled(self, fn) -> np.ndarray:
        """`fn(symbol, positions) -> float | nan`, averaged across markets."""
        out = np.full(N_BOOT, np.nan)
        for b, draw in enumerate(self.draws):
            vals = [fn(s, draw[s]) for s in self.symbols]
            vals = [v for v in vals if v is not None and np.isfinite(v)]
            if vals:
                out[b] = float(np.mean(vals))
        return out


def summarize(samples: np.ndarray) -> dict[str, Any]:
    a = samples[np.isfinite(samples)]
    if a.size < N_BOOT // 4:
        return {"point": None, "ci95": None, "n_boot_valid": int(a.size)}
    point = float(np.mean(a))
    sd = float(np.std(a, ddof=1))
    lo, hi = point - Z * sd, point + Z * sd
    return {
        "point": point,
        "sd": sd,
        "ci95": [lo, hi],
        "ci_halfwidth": float(Z * sd),
        "excludes_zero": bool(lo > 0 or hi < 0),
        "n_boot_valid": int(a.size),
    }


def cell_stats(df: pd.DataFrame, mask: np.ndarray, value: str) -> dict[str, Any]:
    sub = df.loc[mask]
    return {
        "n_bars": int(len(sub)),
        "n_legs": int(sub["leg_id"].nunique()) if len(sub) else 0,
        "point": float(sub[value].mean()) if len(sub) else None,
    }


def write_artifact(payload: dict[str, Any]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = OUT_DIR / f"{payload['experiment']}_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {path.relative_to(ROOT)}", flush=True)
    return path


def base_payload(experiment: str) -> dict[str, Any]:
    return {
        "prereg_doc": PREREG_DOC,
        "experiment": experiment,
        "free_params": FREE_PARAMS[experiment],
        "protocol": {
            "cv": CV_KWARGS, "n_boot": N_BOOT, "seed": SEED,
            "alpha": ALPHA, "z": Z, "markets": list(WATCH_MARKETS),
            "label": "gold_labels(min_cycle=60), EVAL ONLY",
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "교육·연구 목적의 국면 인식 보조 자료이며 투자 권유·자문이 아닙니다.",
    }


# ------------------------------------------------------------------ P-DJ-1


def run_dj1(frames: dict[str, pd.DataFrame], boot: LegBootstrap) -> dict[str, Any]:
    """Is momo more accurate on bars where all four heads agree on side?"""
    out = base_payload("P-DJ-1")
    kills: list[str] = []

    def agree_mask(df: pd.DataFrame, unanimous: bool) -> np.ndarray:
        n = df["n_side_agree"].to_numpy()
        return (n == 4) if unanimous else (n <= 3)

    cells: dict[str, Any] = {}
    for sym, df in frames.items():
        for label, unanimous in (("agree4", True), ("agree_le3", False)):
            cells[f"{sym}|{label}"] = cell_stats(df, agree_mask(df, unanimous), "momo_hit")
    # Reported only. The binning that decides anything is the frozen binary one;
    # re-cutting after seeing these would be fitting by selection.
    for sym, df in frames.items():
        for k in (1, 2, 3, 4):
            cells[f"{sym}|n_agree={k}(report_only)"] = cell_stats(
                df, df["n_side_agree"].to_numpy() == k, "momo_hit"
            )

    for sym, df in frames.items():
        for label, unanimous in (("agree4", True), ("agree_le3", False)):
            if cells[f"{sym}|{label}"]["n_legs"] < FREE_PARAMS["P-DJ-1"]["J3"]:
                kills.append(f"K-DJ-1:{sym}|{label}")

    def delta(sym: str, pos: np.ndarray) -> float | None:
        df = frames[sym].iloc[pos]
        a, b = df.loc[agree_mask(df, True)], df.loc[agree_mask(df, False)]
        if a.empty or b.empty:
            return None
        return float(a["momo_hit"].mean() - b["momo_hit"].mean())

    pooled = summarize(boot.pooled(delta))
    per_market = {
        s: summarize(
            np.array([
                (delta(s, boot.draws[b][s]) if delta(s, boot.draws[b][s]) is not None else np.nan)
                for b in range(N_BOOT)
            ])
        )
        for s in frames
    }
    signs = {s: np.sign(v["point"]) for s, v in per_market.items() if v.get("point") is not None}
    if len(set(signs.values())) > 1:
        kills.append("K-DJ-2:market_sign_disagreement")

    j2 = FREE_PARAMS["P-DJ-1"]["J2"]
    if kills or pooled.get("point") is None:
        verdict = "not_identified"
    elif abs(pooled["point"]) < pooled["ci_halfwidth"]:
        verdict = "not_identified"
    elif pooled["point"] >= j2 and pooled["excludes_zero"]:
        verdict = "adopted"
    else:
        verdict = "rejected"

    out.update({
        "cells": cells,
        "delta_pooled": pooled,
        "delta_per_market": per_market,
        "gates": {"adopt_threshold_J2": j2},
        "kills_fired": kills,
        "verdict": verdict,
        "display_allowed": verdict == "adopted",
    })
    return out


# ---------------------------------------------------------------- P-COND-1


def run_cond1(frames: dict[str, pd.DataFrame], boot: LegBootstrap) -> dict[str, Any]:
    """Tier x horizon: how often the gold side at t+h still matched the call."""
    out = base_payload("P-COND-1")
    p = FREE_PARAMS["P-COND-1"]
    kills: list[str] = []
    cells: dict[str, Any] = {}
    permitted = 0
    tiers = ("unanimous", "strong", "lean", "mixed")

    for sym, df in frames.items():
        for tier in tiers:
            for h in p["J17"]:
                col = f"gold_side_fwd{h}"
                ok = (df["tier"] == tier).to_numpy() & df[col].notna().to_numpy()
                sub = df.loc[ok]
                key = f"{sym}|{tier}|h{h}"
                if sub.empty:
                    cells[key] = {"n_bars": 0, "n_legs": 0, "point": None, "permitted": False}
                    continue

                def stat(s: str, pos: np.ndarray, _tier=tier, _col=col) -> float:
                    d = frames[s].iloc[pos]
                    m = (d["tier"] == _tier).to_numpy() & d[_col].notna().to_numpy()
                    if not m.any():
                        return np.nan
                    dd = d.loc[m]
                    return float((dd[_col] == dd["momo_side"]).mean())

                samples = np.array([stat(sym, boot.draws[b][sym]) for b in range(N_BOOT)])
                summ = summarize(samples)
                n_legs = int(sub["leg_id"].nunique())
                # Per-market legs and per-market transcription: a tier table is
                # never borrowed across markets (confidence_spec §5).
                allow = (
                    n_legs >= p["J18"]
                    and summ.get("ci_halfwidth") is not None
                    and summ["ci_halfwidth"] <= p["J19"]
                )
                cells[key] = {
                    "n_bars": int(len(sub)),
                    "n_legs": n_legs,
                    "point": float((sub[col] == sub["momo_side"]).mean()),
                    "ci95": summ.get("ci95"),
                    "ci_halfwidth": summ.get("ci_halfwidth"),
                    "permitted": bool(allow),
                }
                permitted += int(allow)

    # K-CD-1: a horizon where no two tiers separate is a restatement of the
    # unconditional base rate, so the column goes rather than getting dressed up.
    dead_columns: list[int] = []
    for h in p["J17"]:
        separated = False
        for sym in frames:
            good = [c for t in tiers if (c := cells[f"{sym}|{t}|h{h}"]).get("ci95")]
            for i in range(len(good)):
                for j in range(i + 1, len(good)):
                    a, b = good[i]["ci95"], good[j]["ci95"]
                    if a[1] < b[0] or b[1] < a[0]:
                        separated = True
        if not separated:
            dead_columns.append(h)
            kills.append(f"K-CD-1:h{h}")

    # K-CD-1 deletes the column, so a cell in a dead column is not available to
    # be counted — the deletion has to land before K-CD-3 counts survivors, or a
    # table whose every column was struck can still report itself as displayable.
    surviving = sum(
        1
        for key, c in cells.items()
        if c["permitted"] and int(key.rsplit("|h", 1)[1]) not in dead_columns
    )
    if surviving < p["J20"]:
        kills.append(f"K-CD-3:only_{surviving}_cells_survive_column_deletion")
        verdict = "rejected"
    else:
        verdict = "adopted"

    out.update({
        "cells": cells,
        "n_cells_permitted": permitted,
        "n_cells_surviving_deletion": surviving,
        "dead_columns": dead_columns,
        "gates": {
            "min_legs_J18": p["J18"],
            "max_ci_halfwidth_J19": p["J19"],
            "min_permitted_cells_J20": p["J20"],
        },
        "kills_fired": kills,
        "verdict": verdict,
        "display_allowed": verdict == "adopted",
        "note": "No return, price or volatility column exists in this artifact (prereg 6.1 / K-CD-2).",
    })
    return out


# ---------------------------------------------------------------- P-FLIP-1


def run_flip1(frames: dict[str, pd.DataFrame], boot: LegBootstrap) -> dict[str, Any]:
    """Do bars whose call is cheap to flip actually flip more often?"""
    out = base_payload("P-FLIP-1")
    p = FREE_PARAMS["P-FLIP-1"]
    kills: list[str] = []

    # Terciles from train slices only. Each OOS bar carries its fold id, so its
    # cut comes from the folds that precede it — never from the block it sits in.
    for df in frames.values():
        band = np.full(len(df), "", dtype=object)
        v = df["flip_move"].to_numpy()
        for f in sorted(df["fold"].unique()):
            prior = df.loc[df["fold"] < f, "flip_move"].dropna()
            if len(prior) < 60:
                continue  # no admissible cut yet; those bars stay unbanded
            lo, hi = np.nanquantile(prior, [1 / 3, 2 / 3])
            m = (df["fold"] == f).to_numpy() & np.isfinite(v)
            band[m & (v <= lo)] = "near"
            band[m & (v > lo) & (v <= hi)] = "mid"
            band[m & (v > hi)] = "far"
        df["flip_band"] = band

    events = int(sum(f["flips_within_lag"].fillna(0).sum() for f in frames.values()))
    if events < p["J6"]:
        kills.append(f"K-FLIP-1:only_{events}_transitions")

    cells = {
        f"{sym}|{b}": cell_stats(df, (df["flip_band"] == b).to_numpy(), "flips_within_lag")
        for sym, df in frames.items()
        for b in ("near", "mid", "far")
    }

    def delta(sym: str, pos: np.ndarray, shuffled: bool = False) -> float:
        df = frames[sym].iloc[pos]
        band = df["flip_band"].to_numpy()
        if shuffled:
            band = np.random.default_rng(SEED + int(pos.sum() % 2**31)).permutation(band)
        y = df["flips_within_lag"].to_numpy()
        near, far = y[band == "near"], y[band == "far"]
        near, far = near[np.isfinite(near)], far[np.isfinite(far)]
        if near.size == 0 or far.size == 0:
            return np.nan
        return float(near.mean() - far.mean())

    causal = summarize(boot.pooled(lambda s, pos: delta(s, pos)))
    canary = summarize(boot.pooled(lambda s, pos: delta(s, pos, shuffled=True)))

    # K-FLIP-2: if the shuffled arm's interval covers the causal point, the gain
    # is indistinguishable from plumbing (XS_BREADTH 4 precedent).
    canary_pass = True
    if causal.get("point") is not None and canary.get("ci95"):
        lo, hi = canary["ci95"]
        canary_pass = not (lo <= causal["point"] <= hi)
    if not canary_pass:
        kills.append("K-FLIP-2:shuffle_canary_indistinguishable")

    if causal.get("point") is None:
        verdict = "not_identified"
    elif kills:
        verdict = "rejected"
    elif causal["point"] >= p["J5"] and causal["excludes_zero"] and canary_pass:
        verdict = "adopted"
    else:
        verdict = "rejected"

    out.update({
        "cells": cells,
        "n_transitions": events,
        "delta_pooled": causal,
        "canary": {"shuffled_delta": canary, "passed": canary_pass},
        "gates": {"adopt_threshold_J5": p["J5"], "min_events_J6": p["J6"]},
        "kills_fired": kills,
        "verdict": verdict,
        "display_allowed": verdict == "adopted",
    })
    return out


# ----------------------------------------------------------------- P-RUN-1


def run_run1(frames: dict[str, pd.DataFrame], boot: LegBootstrap) -> dict[str, Any]:
    """Does a long-standing call hit more often, or is that length-biased sampling?"""
    out = base_payload("P-RUN-1")
    p = FREE_PARAMS["P-RUN-1"]
    kills: list[str] = []
    lo_cut, hi_cut = p["J7"]
    bands = ("1-20", "21-60", "61+")

    for df in frames.values():
        v = df["run_len"].to_numpy()
        df["run_band"] = np.where(v <= lo_cut, "1-20", np.where(v <= hi_cut, "21-60", "61+"))

    cells = {
        f"{sym}|{b}": cell_stats(df, (df["run_band"] == b).to_numpy(), "momo_hit")
        for sym, df in frames.items()
        for b in bands
    }
    for key, c in cells.items():
        if c["n_legs"] < p["J9"]:
            kills.append(f"K-RUN-1:{key}")

    # K-RUN-3: a cell carried by two or three legs restates those particular
    # market episodes, not a property of long-standing calls.
    for sym, df in frames.items():
        sub = df.loc[df["run_band"] == "61+"]
        if len(sub):
            top3 = float(sub["leg_id"].value_counts().head(3).sum()) / len(sub)
            if top3 > p["J10"]:
                kills.append(f"K-RUN-3:{sym}|top3_legs={top3:.2f}")

    def band_means(df: pd.DataFrame) -> dict[str, float]:
        return {
            b: float(df.loc[df["run_band"] == b, "momo_hit"].mean())
            for b in bands
            if (df["run_band"] == b).any()
        }

    def delta(sym: str, pos: np.ndarray, placebo: bool = False) -> float:
        df = frames[sym].iloc[pos]
        if placebo:
            # Prereg §4.3: permute the SIDE CALL within each cell, then re-score
            # against gold. Permuting the hit instead is a no-op — a within-group
            # shuffle cannot move that group's mean — which is how the first run
            # of this produced a placebo identical to the observed arm to four
            # decimals. What has to be destroyed is the call/gold alignment,
            # while the cell structure and each cell's mix of calls stay put.
            df = df.copy()
            rng = np.random.default_rng(SEED + int(pos.sum() % 2**31))
            side = df["momo_side"].to_numpy().copy()
            bandv = df["run_band"].to_numpy()
            for b in bands:
                m = bandv == b
                if m.sum() > 1:
                    side[m] = rng.permutation(side[m])
            df["momo_hit"] = (side == df["gold_side"].to_numpy()).astype(float)
        means = band_means(df)
        if len(means) < 2:
            return np.nan
        return float(max(means.values()) - min(means.values()))

    observed = summarize(boot.pooled(lambda s, pos: delta(s, pos)))
    placebo = summarize(boot.pooled(lambda s, pos: delta(s, pos, placebo=True)))

    # K-RUN-2: a placebo that separates from zero means the cell structure alone
    # manufactures a gap, and the observed one cannot be read as skill.
    placebo_clean = not bool(placebo.get("excludes_zero"))
    if not placebo_clean:
        kills.append("K-RUN-2:placebo_separates_from_zero")

    point_means = {s: band_means(df) for s, df in frames.items()}

    def _monotone(m: dict[str, float]) -> bool:
        seq = [m[b] for b in bands if b in m]
        return seq == sorted(seq) or seq == sorted(seq, reverse=True)

    monotone = all(_monotone(m) for m in point_means.values())

    if "K-RUN-2:placebo_separates_from_zero" in kills:
        verdict = "rejected"
    elif kills:
        verdict = "not_identified"
    elif observed.get("point") is None:
        verdict = "not_identified"
    elif observed["point"] >= p["J8"] and observed["excludes_zero"] and monotone:
        verdict = "adopted"
    else:
        verdict = "rejected"

    out.update({
        "cells": cells,
        "band_means": point_means,
        "delta_observed": observed,
        "placebo": {"delta": placebo, "clean": placebo_clean},
        "monotone": bool(monotone),
        "gates": {
            "adopt_threshold_J8": p["J8"],
            "min_legs_J9": p["J9"],
            "max_leg_concentration_J10": p["J10"],
        },
        "kills_fired": kills,
        "verdict": verdict,
        "display_allowed": verdict == "adopted",
    })
    return out


RUNNERS = {
    "P-DJ-1": run_dj1,
    "P-COND-1": run_cond1,
    "P-FLIP-1": run_flip1,
    "P-RUN-1": run_run1,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", choices=sorted(RUNNERS))
    args = ap.parse_args()

    assert_free_params_match_prereg()
    print(f"prereg check OK - running {args.only or sorted(RUNNERS)}", flush=True)

    frames: dict[str, pd.DataFrame] = {}
    for sym in WATCH_MARKETS:
        built = build_market(sym)
        if built is None:
            print(f"FATAL: {sym} unavailable", flush=True)
            return 1
        if "fold_failure" in built:
            print(f"FATAL K-DJ-3: {built['fold_failure']}", flush=True)
            return 1
        frames[sym] = built["oos"]
        # Persist the scored frame. Rebuilding it costs a full walk-forward
        # refit, and a result nobody can interrogate without paying that twice
        # is a result nobody checks.
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in sym)
        built["oos"].to_csv(OUT_DIR / f"oos_frame_{safe}.csv")
        if not test_flip_identity(sym, load_market(sym).ohlcv["close"].astype(float)):
            print("FATAL: flip boundary identity failed", flush=True)
            return 1

    boot = LegBootstrap(frames)
    print(f"bootstrap ready: {N_BOOT} shared draws over {len(frames)} markets", flush=True)

    for name in args.only or sorted(RUNNERS):
        print(f"\n== {name} ==", flush=True)
        payload = RUNNERS[name](frames, boot)
        write_artifact(payload)
        print(
            f"  verdict={payload['verdict']} display_allowed={payload['display_allowed']} "
            f"kills={payload['kills_fired']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
