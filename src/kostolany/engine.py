"""End-to-end Kostolany engine: data → features → model → gauges → egg point."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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


ModelKind = Literal["hmm", "gbm", "ensemble", "tsfm", "ensemble_v3"]


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
    evidence: list[dict[str, Any]] | None = None


class KostolanyEngine:
    def __init__(self, model_kind: ModelKind = "ensemble") -> None:
        self.model_kind = model_kind
        self.model: Any = None
        self.symbol: str | None = None
        self._last_features: pd.DataFrame | None = None
        self._last_ohlcv: pd.DataFrame | None = None
        self._last_pred: RegimePrediction | None = None

    def _make_model(self) -> Any:
        if self.model_kind == "hmm":
            return KostolanyHMM()
        if self.model_kind == "gbm":
            return KostolanyGBM()
        if self.model_kind in {"tsfm", "ensemble_v3"}:
            return TSFMEnsemble()
        return EnsembleEngine()

    def fit_market(self, market: MarketData) -> "KostolanyEngine":
        feats = build_features(market.ohlcv, market.extras)
        X = model_matrix(feats).dropna()
        if len(X) < 50:
            raise ValueError(
                f"Not enough clean feature rows for {market.symbol}: {len(X)} "
                "(check FRED/extra merges for all-NaN columns)"
            )
        y = weak_labels(feats).reindex(X.index)
        self.model = self._make_model()
        self.model.fit(X, y)
        self.symbol = market.symbol
        self._last_features = feats
        self._last_ohlcv = market.ohlcv
        self._last_pred = self.model.predict(X)
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
        evidence = _build_evidence(self._last_features.loc[ts], gauges)
        ex, ey = egg_coordinate(proba_row)
        next_likely = self._transition_hints(regime, pred, at=ts)
        tscore = None
        if hasattr(self.model, "last_traj_") and self.model.last_traj_ is not None:
            try:
                tscore = float(self.model.last_traj_.transition_score.loc[ts])
            except Exception:  # noqa: BLE001
                tscore = None

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
            evidence=evidence,
        )

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


def _build_evidence(feat_row: pd.Series, gauges: dict) -> list[dict[str, Any]]:
    """Human-readable evidence rows for the watch UI."""
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
            "detail": "위험선호·변동성 기반 심리",
        },
        {
            "key": "position",
            "label": "위치·추세",
            "value": float(1.0 / (1.0 + abs(min(dd, 0)) * 4)),  # shallower DD → higher
            "level": "낙폭" if dd < -0.12 else ("상승" if ret20 > 0.03 else "횡보"),
            "detail": f"20일 수익 {ret20*100:+.1f}% · 고점대비 {dd*100:.1f}% · 추세 {trend:+.2f}",
        },
    ]
