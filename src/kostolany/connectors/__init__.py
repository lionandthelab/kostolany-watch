"""Public connector facade."""

from __future__ import annotations

import pandas as pd

from kostolany.connectors.fred import fetch_fred_panel, fred_to_extras
from kostolany.connectors.krx import fetch_krx
from kostolany.data import MarketData, fetch_yahoo, make_synthetic
from kostolany.settings import get_settings


def merge_extras(base: pd.DataFrame | None, *others: pd.DataFrame | None) -> pd.DataFrame | None:
    frames = [f for f in (base, *others) if f is not None and not f.empty]
    if not frames:
        return None
    out = frames[0]
    for f in frames[1:]:
        out = out.join(f, how="outer", rsuffix="_dup")
        out = out[[c for c in out.columns if not str(c).endswith("_dup")]]
    return out.sort_index().ffill()


def load_market(
    symbol: str,
    *,
    start: str | None = None,
    enrich_fred: bool = True,
) -> MarketData:
    """Unified loader: SYNTH / KS11|KRX / Yahoo tickers, optional FRED extras."""
    settings = get_settings()
    start = start or settings.data_start
    sym = symbol.strip().upper()

    if sym in {"SYNTH", "SYNTHETIC"}:
        market, _ = make_synthetic(n=2000, seed=42)
        return market

    if sym in {"KS11", "KRX", "KOSPI", "^KS11"}:
        market = fetch_krx(start=start)
    else:
        # map friendly names
        yahoo_map = {"SPX": "^GSPC", "S&P500": "^GSPC", "BTC": "BTC-USD"}
        ysym = yahoo_map.get(sym, symbol)
        market = fetch_yahoo(ysym, start=start)

    fred_extras = None
    if enrich_fred:
        try:
            panel = fetch_fred_panel(start=start)
            fred_extras = fred_to_extras(panel).reindex(market.ohlcv.index).ffill()
        except Exception:  # noqa: BLE001
            fred_extras = None

    extras = merge_extras(market.extras, fred_extras)
    return MarketData(symbol=market.symbol, ohlcv=market.ohlcv, extras=extras)


__all__ = [
    "fetch_fred_panel",
    "fred_to_extras",
    "fetch_krx",
    "load_market",
    "merge_extras",
]
