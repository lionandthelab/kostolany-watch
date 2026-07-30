"""FRED macro / liquidity / sentiment connectors (point-in-time aligned as published)."""

from __future__ import annotations


import httpx
import numpy as np
import pandas as pd

from kostolany.connectors.cache import read_cache, write_cache
from kostolany.settings import get_settings

# Core series used as money / credit / fear proxies
FRED_SERIES = {
    "FEDFUNDS": "fed_funds",
    "M2SL": "m2",
    "T10Y2Y": "yield_curve",
    "VIXCLS": "vix",
    "BAA10Y": "credit_spread",
}

# Publication lag in CALENDAR days, applied to each series' observation index
# BEFORE the business-day resample. FRED stamps the June M2SL print on Jun-01,
# but the H.6 release lands ~4 weeks into the following month — without this
# shift the feature panel sees macro prints ~1-2 months before they existed.
# Market-close series (VIX) are public in real time; index-computed series
# (BAA10Y) post next day. This is the S-size fix, NOT an ALFRED vintage rebuild.
FRED_PUBLICATION_LAG_DAYS = {
    "FEDFUNDS": 31,
    "M2SL": 57,
    "T10Y2Y": 0,
    "VIXCLS": 0,
    "BAA10Y": 1,
}


def _fetch_fred_series(series_id: str, api_key: str, start: str) -> pd.Series:
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
    }
    with httpx.Client(timeout=get_settings().http_timeout) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        obs = r.json().get("observations", [])
    idx, vals = [], []
    for row in obs:
        v = row.get("value")
        if v in (None, "."):
            continue
        idx.append(pd.Timestamp(row["date"]))
        vals.append(float(v))
    return pd.Series(vals, index=pd.DatetimeIndex(idx), name=series_id).sort_index()


def _yahoo_proxy_panel(start: str) -> pd.DataFrame:
    """Offline-friendly proxies when FRED key is absent."""
    import yfinance as yf

    tickers = {
        "^VIX": "vix",
        "^TNX": "tnx",  # 10Y yield
        "^IRX": "irx",  # 13W bill ~ short rate
    }
    frames = {}
    for t, name in tickers.items():
        hist = yf.Ticker(t).history(start=start, auto_adjust=True)
        if hist.empty:
            continue
        s = hist["Close"].astype(float)
        s.index = pd.to_datetime(s.index).tz_localize(None)
        frames[name] = s
    if not frames:
        raise ValueError("Yahoo macro proxies unavailable")
    df = pd.DataFrame(frames).sort_index().ffill()
    # Approximate FRED-style columns
    out = pd.DataFrame(index=df.index)
    out["vix"] = df.get("vix")
    out["fed_funds"] = df.get("irx")
    out["yield_curve"] = (df.get("tnx") - df.get("irx")).astype(float) if "tnx" in df and "irx" in df else np.nan
    out["m2"] = np.nan  # no reliable free daily M2 via Yahoo
    # No fabricated credit_spread: a vix*0.05 stand-in made credit_proxy and
    # sentiment_override perfectly collinear. Absent means absent; the feature
    # layer falls back to its own (distinct) local proxy.
    out["credit_spread"] = np.nan
    return out


def fetch_fred_panel(start: str | None = None, *, use_cache: bool = True) -> pd.DataFrame:
    """Return daily-ish panel of macro features (forward-filled from native frequencies)."""
    settings = get_settings()
    start = start or settings.data_start
    # v3: publication lags applied + fabricated credit_spread removed. Old v2
    # blobs carry look-ahead macro prints and must never be reused.
    cache_key = f"fred_panel_v3_{start}"
    if use_cache:
        cached = read_cache(cache_key, max_age_hours=24)
        if cached is not None and not cached.empty:
            return cached

    key = settings.fred_api_key
    if key:
        cols = {}
        for sid, name in FRED_SERIES.items():
            try:
                s = _fetch_fred_series(sid, key, start)
                lag = FRED_PUBLICATION_LAG_DAYS.get(sid, 0)
                if lag:
                    s = s.copy()
                    s.index = s.index + pd.Timedelta(days=lag)
                cols[name] = s
            except Exception:  # noqa: BLE001
                continue
        if not cols:
            panel = _yahoo_proxy_panel(start)
        else:
            panel = pd.DataFrame(cols).sort_index()
    else:
        panel = _yahoo_proxy_panel(start)

    # Resample to business day then forward-fill monthly/irregular prints
    panel = panel.resample("B").last().ffill()
    write_cache(cache_key, panel)
    return panel


def fred_to_extras(panel: pd.DataFrame) -> pd.DataFrame:
    """Map FRED panel → feature extras expected by `build_features`."""
    df = panel.copy().ffill()

    def _z(s: pd.Series, w: int = 126) -> pd.Series:
        s = s.ffill()
        mu = s.rolling(w, min_periods=20).mean()
        sd = s.rolling(w, min_periods=20).std().replace(0, np.nan)
        return ((s - mu) / sd).clip(-6, 6).fillna(0.0)

    money = pd.Series(0.0, index=df.index)
    if "fed_funds" in df and df["fed_funds"].notna().any():
        money = money - _z(df["fed_funds"])  # falling rates → easier money
    if "m2" in df and df["m2"].notna().sum() > 50:
        money = money + _z(df["m2"].pct_change(60).fillna(0))
    if "yield_curve" in df and df["yield_curve"].notna().any():
        money = money + 0.5 * _z(df["yield_curve"])

    credit = pd.Series(0.0, index=df.index)
    if "credit_spread" in df and df["credit_spread"].notna().any():
        credit = -_z(df["credit_spread"])
    elif "vix" in df and df["vix"].notna().any():
        credit = -0.5 * _z(df["vix"])

    sent = pd.Series(0.0, index=df.index)
    if "vix" in df and df["vix"].notna().any():
        sent = -_z(df["vix"])  # high VIX → fear

    return pd.DataFrame(
        {
            "money_proxy": money,
            "credit_proxy": credit,
            "sentiment_override": sent,
            "vix": df["vix"] if "vix" in df else np.nan,
            "fed_funds": df["fed_funds"] if "fed_funds" in df else np.nan,
        },
        index=df.index,
    )
