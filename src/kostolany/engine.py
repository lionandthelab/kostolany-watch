"""End-to-end Kostolany engine: data → features → model → gauges → egg point."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Literal

import joblib
import pandas as pd

from kostolany.connectors import load_market
from kostolany.data import MarketData, make_synthetic
from kostolany.features import build_features, gauge_scores, model_matrix
from kostolany.harness.metrics import egg_coordinate
from kostolany.labels import gold_labels, weak_labels
from kostolany.models import EnsembleEngine, KostolanyGBM, KostolanyHMM, RegimePrediction
from kostolany.regimes import DISCLAIMER_KO, REGIME_META, CYCLE_EDGES, Regime
from kostolany.replay import ReplayFrame, build_replay, replay_payload
from kostolany.tsfm import TSFMEnsemble


ModelKind = Literal["hmm", "gbm", "ensemble", "tsfm", "ensemble_v3", "momo"]
MODEL_FIT_LOCK = RLock()

log = logging.getLogger(__name__)

#: Conviction tier by majority size. Single source for `_vote_block` and the
#: flip ladder — a second copy would let the two disagree about the same split.
VOTE_TIER_BY_MAJORITY = {8: "unanimous", 7: "strong", 6: "lean"}


@dataclass
class EngineSnapshot:
    symbol: str
    asof: str
    regime: str
    regime_name_ko: str
    confidence: float
    probabilities: dict[str, float]
    gauges: dict[str, float]
    egg: dict[str, float]
    action_ko: str
    next_likely: list[dict[str, Any]]
    disclaimer: str
    transition_score: float | None = None
    context_gauges: list[dict[str, Any]] | None = None
    # momo head only: live 8-rule vote block (deterministic counts; the UI's
    # conviction tier keys off this). None for the AI heads and on the
    # fail-closed path (vote side disagreeing with the served regime side).
    vote: dict[str, Any] | None = None
    # momo head only, both inheriting `vote`'s fail-closed discipline:
    #   flip  same-bar counterfactual distances at which the call changes (C3)
    #   run   trailing run-length of the served call (C4)
    # None for the AI heads — a fitted model's regime run is a restatement of
    # the fit, not the rule product the copy claims it is.
    flip: dict[str, Any] | None = None
    run: dict[str, Any] | None = None


class KostolanyEngine:
    def __init__(self, model_kind: ModelKind = "ensemble") -> None:
        self.model_kind = model_kind
        self.model: Any = None
        self.symbol: str | None = None
        self._last_features: pd.DataFrame | None = None
        self._last_ohlcv: pd.DataFrame | None = None
        self._last_pred: RegimePrediction | None = None
        # momo only: per-rule vote frame aligned exactly like the prediction.
        self._vote_rules: pd.DataFrame | None = None

    def _make_model(self) -> Any:
        if self.model_kind == "hmm":
            return KostolanyHMM()
        if self.model_kind == "gbm":
            return KostolanyGBM()
        if self.model_kind in {"tsfm", "ensemble_v3"}:
            return TSFMEnsemble()
        if self.model_kind == "momo":
            from kostolany.momo import MomoFloorHead

            return MomoFloorHead()
        return EnsembleEngine()

    def fit_market(self, market: MarketData) -> "KostolanyEngine":
        # Cloud Run may schedule watch and flows workers concurrently. Serialize
        # CPU-heavy fits inside one process while priority queues decide order.
        with MODEL_FIT_LOCK:
            feats = build_features(market.ohlcv, market.extras)
            X = model_matrix(feats).dropna()
            if len(X) < 50:
                raise ValueError(
                    f"Not enough clean feature rows for {market.symbol}: {len(X)} "
                    "(check FRED/extra merges for all-NaN columns)"
                )
            y = weak_labels(feats).reindex(X.index)
            self.model = self._make_model()
            if self.model_kind == "momo":
                # The K2 winner consumes PRICES only — it never sees features
                # or labels, so it cannot overfit either.
                close = market.ohlcv["close"].dropna().astype(float).sort_index()
                self.model.fit(close)
                regimes, proba = self.model.predict(close)
                self._last_pred = RegimePrediction(
                    regimes=regimes.reindex(X.index).ffill().astype(int),
                    proba=proba.reindex(X.index).ffill(),
                )
                # Same alignment as the prediction, or the badge could
                # contradict the call it annotates.
                self._vote_rules = self.model.rule_votes(close).reindex(X.index).ffill()
            else:
                self.model.fit(X, y)
                self._last_pred = self.model.predict(X)
            self.symbol = market.symbol
            self._last_features = feats
            self._last_ohlcv = market.ohlcv
        return self

    def fit_symbol(self, symbol: str, start: str = "2010-01-01", *, enrich_fred: bool = True) -> "KostolanyEngine":
        return self.fit_market(load_market(symbol, start=start, enrich_fred=enrich_fred))

    def fit_synthetic(self, n: int = 2500, seed: int = 42) -> tuple["KostolanyEngine", pd.Series]:
        market, truth = make_synthetic(n=n, seed=seed)
        self.fit_market(market)
        return self, truth

    def snapshot(self, asof: str | None = None) -> EngineSnapshot:
        if self._last_pred is None or self._last_features is None:
            raise RuntimeError("Engine has no fitted prediction")
        pred = self._last_pred
        if asof is None:
            ts = pred.regimes.index[-1]
        else:
            ts = pred.regimes.index.asof(pd.Timestamp(asof))
            if pd.isna(ts):
                raise ValueError(f"No regime available at/before {asof}")
        regime_id = int(pred.regimes.loc[ts])
        regime = Regime(regime_id)
        meta = REGIME_META[regime]
        proba_row = pred.proba.loc[ts]
        probs = {Regime(i).name: float(proba_row[f"p{i}"]) for i in range(6)}
        conf = float(max(probs.values()))
        gauges = gauge_scores(self._last_features).loc[ts].to_dict()
        context_gauges = _build_context_gauges(self._last_features.loc[ts], gauges)
        ex, ey = egg_coordinate(proba_row)
        next_likely = self._transition_hints(regime, pred, at=ts)
        tscore = None
        if hasattr(self.model, "last_traj_") and self.model.last_traj_ is not None:
            try:
                tscore = float(self.model.last_traj_.transition_score.loc[ts])
            except Exception:  # noqa: BLE001
                tscore = None

        vote = self._vote_block(ts, regime_id)
        return EngineSnapshot(
            symbol=self.symbol or "UNKNOWN",
            asof=str(pd.Timestamp(ts).date()),
            regime=regime.name,
            regime_name_ko=meta.name_ko,
            confidence=conf,
            probabilities=probs,
            gauges={k: float(v) for k, v in gauges.items()},
            egg={"x": ex, "y": ey},
            action_ko=meta.action_ko,
            next_likely=next_likely,
            disclaimer=DISCLAIMER_KO,
            transition_score=tscore,
            context_gauges=context_gauges,
            vote=vote,
            flip=self._flip_block(ts, regime_id, vote),
            run=self._run_block(ts, regime_id),
        )

    def _vote_block(self, ts: pd.Timestamp, regime_id: int) -> dict[str, Any] | None:
        """Live 8-rule vote at ``ts`` — momo only, fail-closed on any mismatch.

        If, after alignment, the vote's side disagrees with the served regime's
        side, we ship NO vote block rather than a badge contradicting the call.
        """
        if self._vote_rules is None:
            return None
        try:
            from kostolany.momo import RULE_IDS

            row = self._vote_rules.loc[ts]
            up = int(row.sum())
            down = len(row) - up
            side = "up" if up >= down else "down"  # 4-4 tie -> up (disclosed)
            if (side == "up") != (regime_id < 3):
                return None  # fail-closed; UI hides the badge
            n = max(up, down)
            tier = VOTE_TIER_BY_MAJORITY.get(n, "mixed")
            return {
                "up": up,
                "down": down,
                "split": f"{n}-{min(up, down)}",
                "tier": tier,
                "side": side,
                "rules": [
                    {"id": rid, "vote": "up" if bool(row[rid]) else "down"}
                    for rid in RULE_IDS
                ],
            }
        except Exception:  # noqa: BLE001 — the badge must never take the desk down
            return None

    def _flip_block(
        self, ts: pd.Timestamp, regime_id: int, vote: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Where today's call breaks — momo only, fail-closed like `_vote_block`.

        Subjunctive past, not a forecast: every number answers "had today's close
        been X% lower/higher, this rule/tier/side would have gone the other way".
        `momo.rule_flip_levels` makes that an identity with the served vote, so
        there is no fitted quantity anywhere in this block.

        Only `move_pct` ships. Absolute price levels are deliberately absent from
        the payload — that is the one structural defence against the distance
        being read as a support line, and it must stay structural rather than a
        rule the renderer is asked to remember.
        """
        if self.model_kind != "momo" or vote is None or self._last_ohlcv is None:
            return None
        try:
            from kostolany.momo import RULE_IDS

            px = self._last_ohlcv["close"].dropna().astype(float).sort_index()
            if ts not in px.index:
                return None
            px = px.loc[:ts]
            close = float(px.iloc[-1])
            if not math.isfinite(close) or close <= 0:
                return None
            levels = self.model.rule_flip_levels(px)
            if levels.empty:
                return None

            served = {r["id"]: r["vote"] for r in vote["rules"]}
            rules: list[dict[str, Any]] = []
            for rid in RULE_IDS:
                level = float(levels.loc[rid])
                move = level / close - 1.0
                if not math.isfinite(level) or not math.isfinite(move):
                    return None
                # Sign invariant. The closed form must reproduce the vote that
                # was actually served; disagreement means the two are reading
                # different bars (alignment ffill, a revised close), and a
                # distance to the wrong boundary is worse than no distance.
                if (close > level) != (served.get(rid) == "up"):
                    log.warning("flip sign invariant failed for %s at %s", rid, ts)
                    return None
                rules.append({"id": rid, "vote": served[rid], "move_pct": move})

            up = int(vote["up"])
            side_up = up >= 4  # 4-4 ties resolve UP, same as `_vote_block`
            # Moving the close toward the called side's boundaries can only
            # flip called-side rules — the others sit on the far side and stay
            # put — so the ladder is monotone and each step costs exactly one vote.
            ladder = sorted(
                (r for r in rules if (r["vote"] == "up") == side_up),
                key=lambda r: abs(r["move_pct"]),
            )

            def _split_tier(u: int) -> tuple[str, str]:
                n = max(u, 8 - u)
                return f"{n}-{8 - n}", VOTE_TIER_BY_MAJORITY.get(n, "mixed")

            # Rungs needed to take the majority across: down to 3 up-votes from
            # an up call, up to 4 from a down call. The called side always has
            # at least that many rules, so this rung always exists.
            need = up - 3 if side_up else 4 - up

            # Only the rungs BEFORE the side flips. Past it the majority reforms
            # on the opposite side and the tier label climbs back — 6-2 "lean"
            # again, now for the other call. Emitting those would read as this
            # call's grade recovering. Bounded at 3 rows by construction:
            # unanimous -> strong -> lean -> mixed is the whole descent.
            steps: list[dict[str, Any]] = []
            tier = _split_tier(up)[1]
            for k, rung in enumerate(ladder[:need], start=1):
                split, next_tier = _split_tier(up - k if side_up else up + k)
                if next_tier != tier:
                    steps.append(
                        {"split": split, "tier": next_tier, "move_pct": rung["move_pct"]}
                    )
                    tier = next_tier

            side_flip = None
            if 1 <= need <= len(ladder):
                # Side inverts, third does not: `labels_pit.pit_state` never
                # reads bar t, so the turn clock is untouched by this
                # counterfactual and the mirrored call keeps its sector number.
                mirrored = regime_id + 3 if regime_id < 3 else regime_id - 3
                side_flip = {
                    "from": "up" if side_up else "down",
                    "to": "down" if side_up else "up",
                    "regime_to": Regime(mirrored).name,
                    "move_pct": ladder[need - 1]["move_pct"],
                }

            rules.sort(key=lambda r: abs(r["move_pct"]))
            return {
                "basis": "same_bar_close",
                "rules": rules,
                "steps": steps,
                "side_flip": side_flip,
            }
        except Exception:  # noqa: BLE001 — an extra panel must never take the desk down
            return None

    def _run_block(self, ts: pd.Timestamp, regime_id: int) -> dict[str, Any] | None:
        """How long the served call has stood — momo only, a count and nothing more.

        This is today's rules re-applied to past prices, NOT a record of what the
        desk displayed on those days: the ledger's documented contamination
        (auto_adjust back-adjustment) applies in full here and not at all there.
        The UI carries that distinction as fixed copy; the two must never be
        presented as the same fact.
        """
        if self.model_kind != "momo" or self._last_pred is None:
            return None
        try:
            regimes = self._last_pred.regimes.loc[:ts]
            ids = regimes.to_numpy(dtype=int)
            if not len(ids) or int(ids[-1]) != int(regime_id):
                return None
            sides = ids < 3

            def _trailing(arr) -> int:
                n = 1
                while n < len(arr) and arr[-1 - n] == arr[-1]:
                    n += 1
                return n

            side_bars, regime_bars = _trailing(sides), _trailing(ids)
            index = regimes.index
            return {
                "side": "up" if regime_id < 3 else "down",
                "side_bars": int(side_bars),
                "side_since": str(pd.Timestamp(index[-side_bars]).date()),
                # A run reaching the first bar on the grid is a lower bound, not
                # a start date — the UI has to say "at least N bars" for it.
                "side_truncated": bool(side_bars == len(ids)),
                "regime": Regime(regime_id).name,
                "regime_bars": int(regime_bars),
                "regime_since": str(pd.Timestamp(index[-regime_bars]).date()),
                "regime_truncated": bool(regime_bars == len(ids)),
                "grid_bars": int(len(ids)),
            }
        except Exception:  # noqa: BLE001
            return None

    def history(self) -> pd.DataFrame:
        if self._last_pred is None or self._last_features is None:
            raise RuntimeError("Engine has no fitted prediction")
        g = gauge_scores(self._last_features)
        df = pd.DataFrame({"regime": self._last_pred.regimes}).join(self._last_pred.proba).join(g)
        return df

    def replay(self, *, limit: int = 500, stride: int = 1) -> list[ReplayFrame]:
        if self._last_pred is None or self._last_features is None:
            raise RuntimeError("Engine has no fitted prediction")
        prices = self._last_ohlcv["close"] if self._last_ohlcv is not None else None
        return build_replay(self._last_pred, self._last_features, prices=prices, limit=limit, stride=stride)

    def replay_dict(self, *, limit: int = 500, stride: int = 1) -> dict[str, Any]:
        return replay_payload(self.replay(limit=limit, stride=stride), self.symbol or "UNKNOWN")

    def _transition_hints(
        self, current: Regime, pred: RegimePrediction, at: pd.Timestamp | None = None
    ) -> list[dict[str, Any]]:
        hints: list[dict[str, Any]] = []
        row = pred.proba.loc[at] if at is not None else pred.proba.iloc[-1]
        for a, b in CYCLE_EDGES:
            if a != current:
                continue
            p = float(row.get(f"p{int(b)}", 0.0))
            hints.append(
                {
                    "from": a.name,
                    "to": b.name,
                    "to_name_ko": REGIME_META[b].name_ko,
                    "proximity": p,
                    "note": f"{a.name}→{b.name} 사이클 전이 후보",
                }
            )
        # If TSFM transition score available, annotate
        if hasattr(self.model, "last_traj_") and self.model.last_traj_ is not None and at is not None:
            try:
                ts = float(self.model.last_traj_.transition_score.loc[at])
                for h in hints:
                    h["tsfm_transition"] = ts
            except Exception:  # noqa: BLE001
                pass
        hints.sort(key=lambda h: h["proximity"], reverse=True)
        return hints[:3]

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "model_kind": self.model_kind,
                "model": self.model,
                "symbol": self.symbol,
                "features": self._last_features,
                "ohlcv": self._last_ohlcv,
                "pred": self._last_pred,
            },
            path,
        )

    @classmethod
    def load(cls, path: str | Path) -> "KostolanyEngine":
        blob = joblib.load(path)
        eng = cls(model_kind=blob["model_kind"])
        eng.model = blob["model"]
        eng.symbol = blob["symbol"]
        eng._last_features = blob["features"]
        eng._last_ohlcv = blob["ohlcv"]
        eng._last_pred = blob["pred"]
        return eng


def fit_analyst_bundle(
    symbol: str,
    start: str = "2010-01-01",
    *,
    enrich_fred: bool = True,
) -> dict[str, KostolanyEngine]:
    """Fit HMM, GBM and direct TSFM views from one shared market/model pass.

    ``TSFMEnsemble`` already owns fitted HMM and GBM arms. Reusing those arms
    removes two duplicate data downloads, feature builds and model fits while
    preserving each analyst's independent posterior/replay.
    """
    if symbol.upper() in {"SYNTH", "SYNTHETIC"}:
        market, _ = make_synthetic(n=2000, seed=42)
    else:
        market = load_market(symbol, start=start, enrich_fred=enrich_fred)

    master = KostolanyEngine(model_kind="tsfm").fit_market(market)
    if not isinstance(master.model, TSFMEnsemble):
        raise RuntimeError("Expected TSFMEnsemble master model")
    if master._last_features is None or master._last_ohlcv is None:
        raise RuntimeError("Master engine missing fitted data")

    X = model_matrix(master._last_features).dropna()
    predictions = {
        "hmm": master.model.hmm.predict(X),
        "gbm": master.model.gbm.predict(X),
        "tsfm": master._last_pred,
    }
    models = {
        "hmm": master.model.hmm,
        "gbm": master.model.gbm,
        "tsfm": master.model,
    }

    # One-scalar lambda-uniform anchor per arm (pre-registered side_head_v1
    # calibration; gate G14). lambda is fitted CAUSALLY: a prefix refit
    # predicts the trailing 252 bars it never saw, and log-loss on the causal
    # weak labels picks the blend. argmax is anchor-invariant, so the regime
    # call never changes — only the displayed probability mass does. Measured
    # unanchored ECE of these arms was 0.75/0.50/0.29 (^GSPC); an anchor
    # failure falls back to lambda=0 == the uniform floor, never a raw vector.
    from kostolany.anchor import apply_lambda, fit_lambda

    lambdas = {"hmm": 0.0, "gbm": 0.0, "tsfm": 0.0}
    hold = 252
    try:
        if len(X) - hold >= 504:
            y_all = weak_labels(master._last_features).reindex(X.index)
            pre = TSFMEnsemble().fit(X.iloc[:-hold], y_all.iloc[:-hold])
            Xh, yh = X.iloc[-hold:], y_all.iloc[-hold:]
            cols = [f"p{i}" for i in range(6)]
            lambdas = {
                "hmm": fit_lambda(pre.hmm.predict(Xh).proba[cols], yh),
                "gbm": fit_lambda(pre.gbm.predict(Xh).proba[cols], yh),
                "tsfm": fit_lambda(pre.predict(Xh).proba[cols], yh),
            }
    except Exception:  # noqa: BLE001 — anchor must never take the desk down
        lambdas = {"hmm": 0.0, "gbm": 0.0, "tsfm": 0.0}

    out: dict[str, KostolanyEngine] = {}
    for kind in ("hmm", "gbm", "tsfm"):
        pred = predictions[kind]
        if pred is None:
            raise RuntimeError(f"Missing prediction for {kind}")
        anchored = RegimePrediction(
            regimes=pred.regimes,
            proba=apply_lambda(pred.proba, lambdas[kind]),
            state_proba=pred.state_proba,
            transition_matrix=pred.transition_matrix,
            mapping=pred.mapping,
        )
        view = KostolanyEngine(model_kind=kind)  # type: ignore[arg-type]
        view.model = models[kind]
        view.symbol = market.symbol
        view._last_features = master._last_features
        view._last_ohlcv = master._last_ohlcv
        view._last_pred = anchored
        view.anchor_lambda = lambdas[kind]
        out[kind] = view

    # Default head (owner decision 2026-07-30): the zero-fitted-parameter
    # momentum vote that won kill condition K2. Nearly free — prices only.
    from kostolany.momo import MomoFloorHead

    close = master._last_ohlcv["close"].dropna().astype(float).sort_index()
    momo_model = MomoFloorHead().fit(close)
    m_regimes, m_proba = momo_model.predict(close)
    momo_view = KostolanyEngine(model_kind="momo")
    momo_view.model = momo_model
    momo_view.symbol = market.symbol
    momo_view._last_features = master._last_features
    momo_view._last_ohlcv = master._last_ohlcv
    momo_view._last_pred = RegimePrediction(
        regimes=m_regimes.reindex(X.index).ffill().astype(int),
        proba=m_proba.reindex(X.index).ffill(),
    )
    momo_view._vote_rules = momo_model.rule_votes(close).reindex(X.index).ffill()
    out["momo"] = momo_view
    return out


def prepare_xy(market: MarketData) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    """Return X, y_weak, y_gold, prices — gold for eval only."""
    feats = build_features(market.ohlcv, market.extras)
    X = model_matrix(feats)
    y_weak = weak_labels(feats)
    y_gold = gold_labels(market.ohlcv["close"])
    return X, y_weak, y_gold, market.ohlcv["close"]


def _level(v: float) -> str:
    if v < 0.35:
        return "낮음"
    if v < 0.65:
        return "보통"
    return "높음"


def _build_context_gauges(feat_row: pd.Series, gauges: dict) -> list[dict[str, Any]]:
    """Human-readable context gauges for the watch UI.

    NOT evidence for the regime call. The shipped head (`momo`) takes prices and
    nothing else (momo.py `MomoFloorHead.fit`), while these rows are derived from
    the FRED-enriched feature matrix. Calling them "evidence" asserted a causal
    story the measurement does not support — the UI states the disjunction
    explicitly above the strip (`t.watch.contextNote`).
    """
    g = {k: float(gauges.get(k, 0.5)) for k in ("volume", "participation", "money", "sentiment")}

    def safe(key: str, default: float = 0.0) -> float:
        try:
            v = feat_row.get(key, default)
            return float(v) if pd.notna(v) else default
        except Exception:  # noqa: BLE001
            return default

    dd = safe("drawdown")
    ret20 = safe("ret_20")
    surge = safe("vol_surge", 1.0)
    trend = safe("trend_slope")

    return [
        {
            "key": "volume",
            "label": "거래량",
            "value": g["volume"],
            "level": _level(g["volume"]),
            "detail": f"회전·거래대금 강도 · 급증배수 {surge:.1f}×",
        },
        {
            "key": "participation",
            "label": "참여",
            "value": g["participation"],
            "level": _level(g["participation"]),
            "detail": "시장 폭·참여 추세 대리 지표",
        },
        {
            "key": "money",
            "label": "돈(유동성)",
            "value": g["money"],
            "level": _level(g["money"]),
            "detail": "금리·신용·유동성 환경 프록시",
        },
        {
            "key": "sentiment",
            "label": "심리",
            "value": g["sentiment"],
            "level": _level(g["sentiment"]),
            # Honest label: this is a price/vol-derived proxy, not survey or
            # flow data, and it is separate from the macro desk's F&G dial.
            "detail": "변동성·수익률 파생 심리 프록시 (공포탐욕 다이얼과 별개)",
        },
        {
            "key": "position",
            "label": "위치·추세",
            "value": float(1.0 / (1.0 + abs(min(dd, 0)) * 4)),  # shallower DD → higher
            "level": "낙폭" if dd < -0.12 else ("상승" if ret20 > 0.03 else "횡보"),
            "detail": f"20일 수익 {ret20*100:+.1f}% · 고점대비 {dd*100:.1f}% · 추세 {trend:+.2f}",
        },
    ]
