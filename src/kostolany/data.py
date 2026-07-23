"""Market data loaders and synthetic regime-aware generators."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from kostolany.regimes import Regime


@dataclass
class MarketData:
    symbol: str
    ohlcv: pd.DataFrame
    extras: pd.DataFrame | None = None


def fetch_yahoo(symbol: str, start: str = "2005-01-01", end: str | None = None) -> MarketData:
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    hist = ticker.history(start=start, end=end, auto_adjust=True)
    if hist.empty:
        raise ValueError(f"No data for {symbol}")
    hist = hist.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )
    hist.index = pd.to_datetime(hist.index).tz_localize(None)
    ohlcv = hist[["open", "high", "low", "close", "volume"]].astype(float)
    return MarketData(symbol=symbol, ohlcv=ohlcv)


def make_synthetic(
    n: int = 2500,
    seed: int = 42,
    start: str = "2000-01-01",
) -> tuple[MarketData, pd.Series]:
    """Generate OHLCV with planted Kostolany-like regimes (ground truth for harness tests)."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start=start, periods=n)

    # Plant a repeating cycle of regimes with variable lengths
    cycle = [Regime.A1, Regime.A2, Regime.A3, Regime.B1, Regime.B2, Regime.B3]
    lengths_base = [80, 120, 70, 60, 110, 90]
    regimes = []
    while len(regimes) < n:
        for r, L in zip(cycle, lengths_base):
            jitter = int(rng.integers(-15, 16))
            regimes.extend([int(r)] * max(30, L + jitter))
    regimes = np.array(regimes[:n], dtype=int)

    # Drift / vol / volume intensity by regime — strongly separable signatures
    drift = {
        int(Regime.A1): 0.0005,
        int(Regime.A2): 0.0010,
        int(Regime.A3): 0.0016,
        int(Regime.B1): -0.0004,
        int(Regime.B2): -0.0010,
        int(Regime.B3): -0.0018,
    }
    vol = {
        int(Regime.A1): 0.007,
        int(Regime.A2): 0.009,
        int(Regime.A3): 0.018,
        int(Regime.B1): 0.010,
        int(Regime.B2): 0.013,
        int(Regime.B3): 0.024,
    }
    vol_lvl = {
        int(Regime.A1): 0.45,
        int(Regime.A2): 1.05,
        int(Regime.A3): 2.2,
        int(Regime.B1): 0.55,
        int(Regime.B2): 1.15,
        int(Regime.B3): 2.4,
    }

    rets = np.array(
        [rng.normal(drift[r], vol[r]) for r in regimes],
        dtype=float,
    )
    close = 100 * np.cumprod(1 + rets)
    noise = rng.normal(0, 0.001, size=n)
    open_ = np.r_[close[0], close[:-1]] * (1 + noise)
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.004, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.004, n))
    volume = rng.lognormal(mean=np.log(1e6), sigma=0.25, size=n) * np.array(
        [vol_lvl[r] for r in regimes]
    )

    ohlcv = pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )
    # Extras: money/credit proxies correlated with planted regimes
    money = np.array(
        [
            0.85
            if r in (int(Regime.A1), int(Regime.A2))
            else 0.55
            if r == int(Regime.A3)
            else 0.25
            for r in regimes
        ],
        dtype=float,
    )
    money = (money + rng.normal(0, 0.04, n)).clip(-2, 2)
    credit = np.array(
        [
            0.8
            if r in (int(Regime.A1), int(Regime.A2), int(Regime.A3))
            else 0.35
            if r != int(Regime.B3)
            else 0.05
            for r in regimes
        ],
        dtype=float,
    ) + rng.normal(0, 0.04, n)
    # Participation extras baked into volume already; add explicit sentiment proxy series
    sent = np.array(
        [
            0.35
            if r == int(Regime.A1)
            else 0.55
            if r == int(Regime.A2)
            else 0.85
            if r == int(Regime.A3)
            else 0.55
            if r == int(Regime.B1)
            else 0.30
            if r == int(Regime.B2)
            else 0.10
            for r in regimes
        ],
        dtype=float,
    ) + rng.normal(0, 0.03, n)
    extras = pd.DataFrame(
        {
            "money_proxy": money,
            "credit_proxy": credit,
            "sentiment_override": sent,
        },
        index=idx,
    )
    truth = pd.Series(regimes, index=idx, name="planted_regime")
    return MarketData(symbol="SYNTH", ohlcv=ohlcv, extras=extras), truth
