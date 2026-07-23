"""Kostolany feature groups: volume, participation, money, sentiment, position."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


FEATURE_GROUPS = ("volume", "participation", "money", "sentiment", "position")


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    group: str
    description: str


FEATURE_SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec("vol_z", "volume", "Volume z-score vs rolling median"),
    FeatureSpec("turnover_z", "volume", "Dollar volume z-score"),
    FeatureSpec("vol_surge", "volume", "Volume / 60d median"),
    FeatureSpec("breadth_proxy", "participation", "Participation breadth proxy"),
    FeatureSpec("participation_trend", "participation", "Rolling participation trend"),
    FeatureSpec("money_proxy", "money", "Liquidity / rate environment proxy"),
    FeatureSpec("credit_proxy", "money", "Credit / funding stress proxy"),
    FeatureSpec("sentiment_proxy", "sentiment", "Risk appetite / fear proxy"),
    FeatureSpec("vol_of_vol", "sentiment", "Realized vol-of-vol"),
    FeatureSpec("ret_20", "position", "20d return"),
    FeatureSpec("ret_60", "position", "60d return"),
    FeatureSpec("drawdown", "position", "Drawdown from rolling peak"),
    FeatureSpec("trend_slope", "position", "Normalized trend slope"),
    FeatureSpec("ma_gap", "position", "Price vs 100d MA gap"),
)


def _zscore(s: pd.Series, window: int = 60) -> pd.Series:
    med = s.rolling(window, min_periods=max(10, window // 3)).median()
    mad = (s - med).abs().rolling(window, min_periods=max(10, window // 3)).median()
    scale = (1.4826 * mad).replace(0, np.nan)
    return ((s - med) / scale).clip(-8, 8)


def build_features(ohlcv: pd.DataFrame, extras: pd.DataFrame | None = None) -> pd.DataFrame:
    """Build point-in-time safe feature matrix from OHLCV (+ optional extras).

    All features use only past/current information (rolling windows ending at t).
    """
    df = ohlcv.copy().sort_index()
    close = df["close"].astype(float)
    volume = df["volume"].astype(float)
    dollar = close * volume

    ret_1 = close.pct_change()
    ret_20 = close.pct_change(20)
    ret_60 = close.pct_change(60)

    peak = close.rolling(252, min_periods=20).max()
    drawdown = close / peak - 1.0

    ma100 = close.rolling(100, min_periods=20).mean()
    ma_gap = close / ma100 - 1.0

    # Trend slope: OLS slope of log-price over 40d, normalized by recent vol
    logp = np.log(close.replace(0, np.nan))
    idx = np.arange(len(logp), dtype=float)

    def _slope(y: np.ndarray) -> float:
        if np.any(~np.isfinite(y)):
            return np.nan
        x = np.arange(len(y), dtype=float)
        x = x - x.mean()
        y = y - y.mean()
        den = (x * x).sum()
        if den == 0:
            return 0.0
        return float((x * y).sum() / den)

    trend_raw = logp.rolling(40, min_periods=20).apply(_slope, raw=True)
    vol20 = ret_1.rolling(20, min_periods=10).std()
    trend_slope = (trend_raw / vol20.replace(0, np.nan)).clip(-8, 8)

    vol_med60 = volume.rolling(60, min_periods=20).median().replace(0, np.nan)
    vol_surge = (volume / vol_med60).clip(0, 20)

    # Participation proxy: rising volume breadth-ish signal from volume persistence
    up_vol = volume.where(ret_1 > 0, 0.0)
    down_vol = volume.where(ret_1 < 0, 0.0)
    breadth_proxy = (
        (up_vol.rolling(20, min_periods=5).sum() - down_vol.rolling(20, min_periods=5).sum())
        / volume.rolling(20, min_periods=5).sum().replace(0, np.nan)
    ).clip(-1, 1)
    participation_trend = breadth_proxy.rolling(40, min_periods=10).mean()
    if extras is not None and "krx_participation_flow" in extras.columns:
        flow = extras["krx_participation_flow"].reindex(df.index).ffill()
        flow_u = 1.0 / (1.0 + np.exp(-0.5 * _zscore(flow.fillna(flow.median()))))
        breadth_proxy = (0.5 * breadth_proxy.fillna(0) + 0.5 * (2 * flow_u - 1)).clip(-1, 1)
        participation_trend = breadth_proxy.rolling(40, min_periods=10).mean()
    elif extras is not None and "participation_trend" in extras.columns:
        participation_trend = (
            0.5 * participation_trend.fillna(0)
            + 0.5 * extras["participation_trend"].reindex(df.index).ffill().fillna(0)
        )

    # Sentiment proxy from realized vol + return skew
    rv = ret_1.rolling(20, min_periods=10).std() * np.sqrt(252)
    vol_of_vol = rv.pct_change(10).rolling(20, min_periods=5).std()
    # High vol + negative returns => fear; low vol + positive => greed
    sentiment_proxy = (
        ret_20.fillna(0) / (rv.replace(0, np.nan) + 1e-6) - 0.5 * _zscore(rv)
    ).clip(-8, 8)
    if extras is not None and "sentiment_override" in extras.columns:
        # Blend optional external sentiment (still causal / point-in-time if sourced that way)
        sentiment_proxy = (
            0.4 * sentiment_proxy.fillna(0)
            + 0.6 * _zscore(extras["sentiment_override"].reindex(df.index).ffill())
        ).clip(-8, 8)

    # Money / liquidity proxies — prefer extras if provided, else derive
    if extras is not None and "money_proxy" in extras.columns:
        money_proxy = extras["money_proxy"].reindex(df.index).ffill()
    else:
        # Falling short-rate proxy via inverse of accelerating returns variance
        money_proxy = (-_zscore(rv, 120)).clip(-8, 8)

    if extras is not None and "credit_proxy" in extras.columns:
        credit_proxy = extras["credit_proxy"].reindex(df.index).ffill()
    else:
        credit_proxy = (-_zscore(vol_of_vol.fillna(0), 60)).clip(-8, 8)

    out = pd.DataFrame(
        {
            "vol_z": _zscore(volume),
            "turnover_z": _zscore(dollar),
            "vol_surge": vol_surge,
            "breadth_proxy": breadth_proxy,
            "participation_trend": participation_trend,
            "money_proxy": money_proxy,
            "credit_proxy": credit_proxy,
            "sentiment_proxy": sentiment_proxy,
            "vol_of_vol": _zscore(vol_of_vol.fillna(0)),
            "ret_20": ret_20,
            "ret_60": ret_60,
            "drawdown": drawdown,
            "trend_slope": trend_slope,
            "ma_gap": ma_gap,
        },
        index=df.index,
    )
    return out


def gauge_scores(features: pd.DataFrame) -> pd.DataFrame:
    """Collapse feature groups into 4 gauges in [0, 1] for UX."""

    def _to_unit(s: pd.Series) -> pd.Series:
        # Robust logistic map of z-like scores into (0,1)
        return 1.0 / (1.0 + np.exp(-0.6 * s.fillna(0)))

    volume = _to_unit(features[["vol_z", "turnover_z"]].mean(axis=1) + 0.25 * (features["vol_surge"] - 1))
    participation = _to_unit(
        features["breadth_proxy"].fillna(0) * 2 + features["participation_trend"].fillna(0) * 2
    )
    money = _to_unit(features[["money_proxy", "credit_proxy"]].mean(axis=1))
    sentiment = _to_unit(features["sentiment_proxy"])
    return pd.DataFrame(
        {
            "volume": volume,
            "participation": participation,
            "money": money,
            "sentiment": sentiment,
        },
        index=features.index,
    )


def model_matrix(features: pd.DataFrame) -> pd.DataFrame:
    """Select and standardize columns used by HMM / classifiers."""
    cols = [f.name for f in FEATURE_SPECS]
    X = features[cols].copy()
    # Causal rolling standardization to avoid full-sample look-ahead
    for c in X.columns:
        mu = X[c].rolling(126, min_periods=30).mean()
        sd = X[c].rolling(126, min_periods=30).std().replace(0, np.nan)
        X[c] = ((X[c] - mu) / sd).clip(-6, 6)
    return X
