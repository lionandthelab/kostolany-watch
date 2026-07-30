"""KRX / Korea market connectors (KOSPI + investor flow proxies)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from kostolany.connectors.cache import read_cache, write_cache
from kostolany.data import MarketData
from kostolany.settings import get_settings


def _from_yahoo_ks11(start: str) -> MarketData:
    from kostolany.data import fetch_yahoo

    return fetch_yahoo("^KS11", start=start)


def _from_fdr(start: str) -> MarketData | None:
    try:
        import FinanceDataReader as fdr
    except ImportError:
        return None
    df = fdr.DataReader("KS11", start)
    if df is None or df.empty:
        return None
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
    need = ["open", "high", "low", "close", "volume"]
    for c in need:
        if c not in df.columns:
            return None
    df.index = pd.to_datetime(df.index)
    return MarketData(symbol="KS11", ohlcv=df[need].astype(float))


def _investor_flow_fdr(start: str) -> pd.DataFrame | None:
    """Foreign/institution net buy proxies when FinanceDataReader supports it."""
    try:
        import FinanceDataReader as fdr
    except ImportError:
        return None
    # Common FDR symbol for KOSPI investor trading value (may vary by version)
    for code in ("KRX/KOSPI", "KS11/FOREIGN"):
        try:
            raw = fdr.DataReader(code, start)
            if raw is not None and not raw.empty:
                out = raw.copy()
                out.index = pd.to_datetime(out.index)
                return out
        except Exception:  # noqa: BLE001
            continue
    return None


def _investor_flow_pykrx(start: str, end: str | None = None) -> pd.DataFrame | None:
    try:
        from pykrx import stock
    except ImportError:
        return None
    end = end or pd.Timestamp.today().strftime("%Y%m%d")
    start_s = pd.Timestamp(start).strftime("%Y%m%d")
    try:
        # Market-wide trading value by investor (KOSPI)
        df = stock.get_market_trading_value_by_date(start_s, end, "KOSPI")
    except Exception:  # noqa: BLE001
        return None
    if df is None or df.empty:
        return None
    df.index = pd.to_datetime(df.index)
    # Normalize column names across pykrx versions
    cols = {c: str(c) for c in df.columns}
    df = df.rename(columns=cols)
    return df.astype(float)


def investor_to_participation(flow: pd.DataFrame) -> pd.Series:
    """Activity-level proxy (magnitude) for the participation gauge.

    ``.abs()`` is intentional HERE ONLY: this series answers "how active is the
    market", not "who is buying". The signed answer lives in
    ``investor_signed_flows`` — never collapse the two again.
    """
    candidates = []
    for key in flow.columns:
        k = str(key).lower()
        if any(tok in k for tok in ("외국", "foreign", "개인", "retail", "기관", "inst")):
            candidates.append(flow[key].abs())
    if not candidates:
        candidates = [flow.iloc[:, i].abs() for i in range(min(3, flow.shape[1]))]
    activity = sum(candidates) / max(len(candidates), 1)
    med = activity.rolling(60, min_periods=10).median().replace(0, np.nan)
    return (activity / med).clip(0, 20).rename("participation_flow")


# Investor-class column detection across pykrx/FDR versions. Order matters:
# "기타법인" must not be captured, and the aggregate ("전체") is skipped.
_INVESTOR_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("foreign_net", ("외국", "foreign")),
    ("inst_net", ("기관", "inst")),
    ("retail_net", ("개인", "retail", "individual")),
)


def investor_signed_flows(flow: pd.DataFrame) -> pd.DataFrame | None:
    """Signed net-buy value per investor class — the sign IS the signal.

    This is the one real supply-demand series the project has (Kostolany's
    "who is buying with whose money"). Returns raw signed KRW value columns
    ``foreign_net`` / ``inst_net`` / ``retail_net``; normalisation is the
    feature layer's job so the cache keeps original units.
    """
    out: dict[str, pd.Series] = {}
    for name, toks in _INVESTOR_TOKENS:
        for key in flow.columns:
            k = str(key).lower()
            if any(tok in k for tok in toks):
                out[name] = flow[key].astype(float)
                break
    if not out:
        return None
    return pd.DataFrame(out)


def fetch_krx(
    start: str | None = None,
    *,
    use_cache: bool = True,
    with_investor: bool = True,
) -> MarketData:
    """Fetch KOSPI OHLCV (+ optional investor-flow extras)."""
    settings = get_settings()
    start = start or settings.data_start
    cache_key = f"krx_ks11_{start}"
    if use_cache:
        cached = read_cache(cache_key, max_age_hours=12)
        if cached is not None and not cached.empty:
            extras = None
            extra_cols = [c for c in cached.columns if c not in ("open", "high", "low", "close", "volume")]
            ohlcv = cached[["open", "high", "low", "close", "volume"]]
            if extra_cols:
                extras = cached[extra_cols]
            return MarketData(symbol="KS11", ohlcv=ohlcv, extras=extras)

    market = _from_fdr(start) or _from_yahoo_ks11(start)
    extras_parts: list[pd.DataFrame] = []

    if with_investor:
        # NOT `a or b`: `or` calls bool() on a DataFrame and raises the moment
        # pykrx actually returns data (latent until KRX credentials exist).
        flow = _investor_flow_pykrx(start)
        if flow is None or flow.empty:
            flow = _investor_flow_fdr(start)
        if flow is not None and not flow.empty:
            part = investor_to_participation(flow)
            # z-ish participation override for gauges
            extras_parts.append(
                pd.DataFrame(
                    {
                        "krx_participation_flow": part,
                        "participation_trend": part.pct_change(20).rolling(20, min_periods=5).mean(),
                    }
                )
            )
            # Signed net-buy per investor class, raw units. Cached so the
            # point-in-time archive starts now; the feature layer decides
            # whether/how a model may consume them (gated experiment).
            signed = investor_signed_flows(flow)
            if signed is not None:
                extras_parts.append(signed.add_prefix("krx_"))

    extras = None
    if extras_parts:
        extras = pd.concat(extras_parts, axis=1).reindex(market.ohlcv.index).ffill()
        market = MarketData(symbol=market.symbol, ohlcv=market.ohlcv, extras=extras)

    store = market.ohlcv.copy()
    if extras is not None:
        store = store.join(extras, how="left")
    write_cache(cache_key, store)
    return market
