"""Historical egg replay frames from fitted engine predictions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from kostolany.features import gauge_scores
from kostolany.harness.metrics import egg_coordinate
from kostolany.models import RegimePrediction
from kostolany.regimes import DISCLAIMER_KO, REGIME_META, Regime


@dataclass
class ReplayFrame:
    date: str
    regime: str
    regime_name_ko: str
    confidence: float
    probabilities: dict[str, float]
    gauges: dict[str, float]
    egg: dict[str, float]
    action_ko: str
    close: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "regime": self.regime,
            "regime_name_ko": self.regime_name_ko,
            "confidence": self.confidence,
            "probabilities": self.probabilities,
            "gauges": self.gauges,
            "egg": self.egg,
            "action_ko": self.action_ko,
            "close": self.close,
        }


def build_replay(
    pred: RegimePrediction,
    features: pd.DataFrame,
    *,
    prices: pd.Series | None = None,
    limit: int | None = 500,
    stride: int = 1,
) -> list[ReplayFrame]:
    """Build chronological egg frames (causal — uses only fitted OOS/in-sample preds)."""
    g = gauge_scores(features)
    df = pd.DataFrame({"regime": pred.regimes}).join(pred.proba).join(g)
    if prices is not None:
        df = df.join(prices.rename("close"))
    df = df.dropna(subset=["regime"]).sort_index()
    if stride > 1:
        df = df.iloc[::stride]
    if limit is not None and len(df) > limit:
        df = df.tail(limit)

    frames: list[ReplayFrame] = []
    for ts, row in df.iterrows():
        rid = int(row["regime"])
        regime = Regime(rid)
        meta = REGIME_META[regime]
        proba = {Regime(i).name: float(row.get(f"p{i}", 0.0)) for i in range(6)}
        # renormalize if needed
        s = sum(proba.values()) or 1.0
        proba = {k: v / s for k, v in proba.items()}
        ex, ey = egg_coordinate(proba)
        frames.append(
            ReplayFrame(
                date=str(pd.Timestamp(ts).date()),
                regime=regime.name,
                regime_name_ko=meta.name_ko,
                confidence=float(max(proba.values())),
                probabilities=proba,
                gauges={
                    k: float(row[k])
                    for k in ("volume", "participation", "money", "sentiment")
                    if k in row and pd.notna(row[k])
                },
                egg={"x": ex, "y": ey},
                action_ko=meta.action_ko,
                close=float(row["close"]) if "close" in row and pd.notna(row["close"]) else None,
            )
        )
    return frames


def replay_payload(frames: list[ReplayFrame], symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "n": len(frames),
        "frames": [f.to_dict() for f in frames],
        "disclaimer": DISCLAIMER_KO,
    }
