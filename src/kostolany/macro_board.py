"""Macro board for the 거시 흐름 desk: rates, inflation, jobs, FG, FedWatch proxy."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import numpy as np
import pandas as pd

from kostolany.settings import get_settings

log = logging.getLogger(__name__)

FEDWATCH_NOTE_KO = (
    "단기금리와 기준금리 격차를 고정 공식으로 변환한 교육용 근사치입니다. "
    "실제 정책 결정 확률로 측정된 값이 아니며, CME FedWatch 공식 확률도 아닙니다."
)
FEDWATCH_NOTE_EN = (
    "Educational proxy: a fixed formula over the short-rate/policy-rate gap. "
    "Not measured against realised policy decisions, and not official CME FedWatch."
)

BOARD_TTL_HOURS = 6.0
_CACHE = "macro_board_v5.json"

# Extra FRED series for the desk (beyond engine feature panel)
BOARD_FRED = {
    "FEDFUNDS": "fed_funds",
    "T10Y2Y": "yield_curve",
    "CPIAUCSL": "cpi",
    "UNRATE": "unemployment",
    "PAYEMS": "payrolls",
    "DGS10": "treasury_10y",
    "DGS2": "treasury_2y",
    "BAMLH0A0HYM2": "hy_oas",
    "T10YIE": "breakeven_10y",
}


def _path():
    return get_settings().cache_path / _CACHE


def _series_points(s: pd.Series, *, n: int = 60) -> list[dict[str, Any]]:
    s = s.dropna().sort_index().tail(n)
    return [
        {"date": str(pd.Timestamp(ts).date()), "value": round(float(v), 4)}
        for ts, v in s.items()
    ]


def _last(s: pd.Series | None) -> float | None:
    if s is None or s.dropna().empty:
        return None
    return float(s.dropna().iloc[-1])


def _yoy(monthly: pd.Series) -> pd.Series:
    return monthly.pct_change(12) * 100.0


def _fetch_fred_board(start: str) -> pd.DataFrame:
    from kostolany.connectors.fred import _fetch_fred_series

    key = get_settings().fred_api_key
    if not key:
        raise ValueError("no FRED key")
    cols: dict[str, pd.Series] = {}
    for sid, name in BOARD_FRED.items():
        try:
            cols[name] = _fetch_fred_series(sid, key, start)
        except Exception as exc:  # noqa: BLE001
            log.warning("FRED %s failed: %s", sid, exc)
    if not cols:
        raise ValueError("empty FRED board")
    return pd.DataFrame(cols).sort_index()


def _yahoo_series(ticker: str, *, start: str | None = None, period: str | None = None) -> pd.Series:
    import yfinance as yf

    hist = (
        yf.Ticker(ticker).history(start=start, auto_adjust=True)
        if start
        else yf.Ticker(ticker).history(period=period or "18mo", auto_adjust=True)
    )
    if hist.empty:
        return pd.Series(dtype=float)
    s = hist["Close"].astype(float)
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s.sort_index()


def _yahoo_board(start: str) -> pd.DataFrame:
    frames: dict[str, pd.Series] = {}
    for t, name in (("^IRX", "fed_funds"), ("^TNX", "treasury_10y"), ("^FVX", "treasury_5y")):
        try:
            s = _yahoo_series(t, start=start)
            if not s.empty:
                frames[name] = s
        except Exception:  # noqa: BLE001
            continue
    if not frames:
        raise ValueError("Yahoo macro board unavailable")
    df = pd.DataFrame(frames).sort_index().ffill()
    if "treasury_10y" in df and "fed_funds" in df:
        df["yield_curve"] = df["treasury_10y"] - df["fed_funds"]
    # No monthly CPI/UNRATE via Yahoo — leave empty
    return df


def _market_extras(start: str) -> dict[str, pd.Series]:
    """Risk / dollar / crypto panels from Yahoo (best-effort)."""
    out: dict[str, pd.Series] = {}
    for t, name in (
        ("^VIX", "vix"),
        ("DX-Y.NYB", "dxy"),
        ("BTC-USD", "btc"),
        ("GC=F", "gold"),
    ):
        try:
            s = _yahoo_series(t, start=start)
            if not s.empty:
                out[name] = s
        except Exception as exc:  # noqa: BLE001
            log.warning("Yahoo extra %s failed: %s", t, exc)
    return out


def _fedwatch_proxy(fed_funds: float | None, irx: float | None) -> dict[str, Any]:
    """Educational stand-in: bill yield vs fed funds → cut / hold / hike bias.

    Not CME FedWatch official probabilities (API/scrape blocked).
    """
    if fed_funds is None or irx is None or not np.isfinite(fed_funds) or not np.isfinite(irx):
        return {
            "label": "Insufficient data",
            "label_en": "Insufficient data",
            "label_ko": "데이터 부족",
            "cut": None,
            "hold": None,
            "hike": None,
            "source": "proxy",
            "note": FEDWATCH_NOTE_EN,
            "note_en": FEDWATCH_NOTE_EN,
            "note_ko": FEDWATCH_NOTE_KO,
        }
    # IRX is discount %; treat as short-rate proxy in same units as FEDFUNDS
    gap = float(irx) - float(fed_funds)  # negative → market prices easier policy
    # Softmax-like mapping of gap (in pp) to three buckets
    cut = float(1.0 / (1.0 + np.exp(8.0 * (gap + 0.05))))
    hike = float(1.0 / (1.0 + np.exp(-8.0 * (gap - 0.15))))
    hold = max(0.0, 1.0 - cut - hike)
    total = cut + hold + hike
    cut, hold, hike = cut / total, hold / total, hike / total
    if cut >= hold and cut >= hike:
        label_en, label_ko = "Cut bias", "인하 쪽 기운"
    elif hike >= hold and hike >= cut:
        label_en, label_ko = "Hike bias", "인상 쪽 기운"
    else:
        label_en, label_ko = "Hold bias", "동결 쪽 기운"
    return {
        "label": label_en,
        "label_en": label_en,
        "label_ko": label_ko,
        "cut": round(cut * 100, 1),
        "hold": round(hold * 100, 1),
        "hike": round(hike * 100, 1),
        "gap_pp": round(gap, 3),
        "source": "IRX vs FEDFUNDS proxy",
        "note": FEDWATCH_NOTE_EN,
        "note_en": FEDWATCH_NOTE_EN,
        "note_ko": FEDWATCH_NOTE_KO,
    }


def compute_macro_board() -> dict[str, Any]:
    start = "2018-01-01"
    try:
        raw = _fetch_fred_board(start)
        source = "FRED"
    except Exception:
        raw = _yahoo_board(start)
        source = "Yahoo proxy"

    # Align monthly series loosely
    fed = raw["fed_funds"].dropna() if "fed_funds" in raw else pd.Series(dtype=float)
    curve = raw["yield_curve"].dropna() if "yield_curve" in raw else pd.Series(dtype=float)
    t10 = raw["treasury_10y"].dropna() if "treasury_10y" in raw else pd.Series(dtype=float)
    cpi = raw["cpi"].dropna() if "cpi" in raw else pd.Series(dtype=float)
    unrate = raw["unemployment"].dropna() if "unemployment" in raw else pd.Series(dtype=float)
    payrolls = raw["payrolls"].dropna() if "payrolls" in raw else pd.Series(dtype=float)
    hy = raw["hy_oas"].dropna() if "hy_oas" in raw else pd.Series(dtype=float)
    bei = raw["breakeven_10y"].dropna() if "breakeven_10y" in raw else pd.Series(dtype=float)

    cpi_yoy = _yoy(cpi) if not cpi.empty else pd.Series(dtype=float)
    payrolls_chg = payrolls.diff() if not payrolls.empty else pd.Series(dtype=float)
    extras = _market_extras(start)
    vix = extras.get("vix", pd.Series(dtype=float))
    dxy = extras.get("dxy", pd.Series(dtype=float))
    btc = extras.get("btc", pd.Series(dtype=float))
    gold = extras.get("gold", pd.Series(dtype=float))

    # Short-rate proxy for FedWatch: prefer IRX from Yahoo if available
    irx_last = None
    try:
        irx = _yahoo_series("^IRX", period="3mo")
        if not irx.empty:
            irx_last = float(irx.iloc[-1])
    except Exception:  # noqa: BLE001
        irx_last = _last(fed)

    from kostolany.crypto_fear_greed import get_crypto_fear_greed
    from kostolany.fear_greed import get_fear_greed

    fg = get_fear_greed(force=False)
    cfg = get_crypto_fear_greed(force=False)

    fed_last = _last(fed)
    t10_last = _last(t10)
    btc_last = _last(btc)
    gold_last = _last(gold)

    def _pct_chg(s: pd.Series, n: int = 20) -> float | None:
        s = s.dropna()
        if len(s) <= n:
            return None
        a, b = float(s.iloc[-1]), float(s.iloc[-1 - n])
        if b == 0 or not np.isfinite(a) or not np.isfinite(b):
            return None
        return round((a / b - 1.0) * 100.0, 2)

    cards = [
        {
            "id": "rates",
            "title": "Policy rate",
            "title_en": "Policy rate",
            "title_ko": "기준금리",
            "value": fed_last,
            "unit": "%",
            "delta": None
            if fed_last is None or len(fed) < 2
            else round(fed_last - float(fed.iloc[-2]), 3),
            "series": _series_points(fed, n=48),
            "blurb": "FEDFUNDS (or short-rate proxy)",
            "blurb_en": "FEDFUNDS (or short-rate proxy)",
            "blurb_ko": "FEDFUNDS (또는 단기금리 프록시)",
        },
        {
            "id": "curve",
            "title": "Yield curve",
            "title_en": "Yield curve",
            "title_ko": "장단기 스프레드",
            "value": _last(curve),
            "unit": "%p",
            "delta": None,
            "series": _series_points(curve, n=48),
            "blurb": "10Y − short rate (T10Y2Y)",
            "blurb_en": "10Y − short rate (T10Y2Y)",
            "blurb_ko": "10Y−단기 (T10Y2Y)",
        },
        {
            "id": "treasury_10y",
            "title": "US 10Y yield",
            "title_en": "US 10Y yield",
            "title_ko": "미 국채 10년",
            "value": t10_last,
            "unit": "%",
            "delta": None
            if t10_last is None or len(t10) < 2
            else round(t10_last - float(t10.iloc[-2]), 3),
            "series": _series_points(t10, n=48),
            "blurb": "DGS10 / ^TNX",
            "blurb_en": "DGS10 / ^TNX",
            "blurb_ko": "DGS10 / ^TNX",
        },
        {
            "id": "cpi",
            "title": "Inflation (CPI YoY)",
            "title_en": "Inflation (CPI YoY)",
            "title_ko": "물가 (CPI YoY)",
            "value": _last(cpi_yoy),
            "unit": "%",
            "delta": None,
            "series": _series_points(cpi_yoy.dropna(), n=48),
            "blurb": "Consumer prices, year-over-year",
            "blurb_en": "Consumer prices, year-over-year",
            "blurb_ko": "소비자물가 전년동월비",
        },
        {
            "id": "breakeven",
            "title": "10Y breakeven",
            "title_en": "10Y breakeven",
            "title_ko": "기대 인플레(10Y)",
            "value": _last(bei),
            "unit": "%",
            "delta": None,
            "series": _series_points(bei, n=48),
            "blurb": "T10YIE market inflation expectation",
            "blurb_en": "T10YIE market inflation expectation",
            "blurb_ko": "T10YIE 시장 기대 인플레이션",
        },
        {
            "id": "jobs",
            "title": "Jobs (unemployment)",
            "title_en": "Jobs (unemployment)",
            "title_ko": "고용 (실업률)",
            "value": _last(unrate),
            "unit": "%",
            "delta": None
            if _last(payrolls_chg) is None
            else round(float(_last(payrolls_chg) or 0), 0),
            "delta_label": "Payrolls chg (k)",
            "delta_label_en": "Payrolls chg (k)",
            "delta_label_ko": "비농 증감(천)",
            "series": _series_points(unrate, n=48),
            "blurb": "UNRATE · PAYEMS change",
            "blurb_en": "UNRATE · PAYEMS change",
            "blurb_ko": "UNRATE · PAYEMS 변화",
        },
        {
            "id": "hy_oas",
            "title": "HY credit spread",
            "title_en": "HY credit spread",
            "title_ko": "하이일드 스프레드",
            "value": _last(hy),
            "unit": "%p",
            "delta": None,
            "series": _series_points(hy, n=48),
            "blurb": "ICE BofA US HY OAS",
            "blurb_en": "ICE BofA US HY OAS",
            "blurb_ko": "ICE BofA 미국 HY OAS",
        },
        {
            "id": "vix",
            "title": "VIX",
            "title_en": "VIX",
            "title_ko": "VIX",
            "value": _last(vix),
            "unit": "",
            "delta": _pct_chg(vix, 5),
            "delta_label": "5d %",
            "delta_label_en": "5d %",
            "delta_label_ko": "5일 %",
            "series": _series_points(vix, n=60),
            "blurb": "Equity vol / fear gauge",
            "blurb_en": "Equity vol / fear gauge",
            "blurb_ko": "주식 변동성·공포 게이지",
        },
        {
            "id": "dxy",
            "title": "US Dollar (DXY)",
            "title_en": "US Dollar (DXY)",
            "title_ko": "달러 인덱스",
            "value": _last(dxy),
            "unit": "",
            "delta": _pct_chg(dxy, 20),
            "delta_label": "20d %",
            "delta_label_en": "20d %",
            "delta_label_ko": "20일 %",
            "series": _series_points(dxy, n=60),
            "blurb": "DX-Y.NYB",
            "blurb_en": "DX-Y.NYB",
            "blurb_ko": "DX-Y.NYB",
        },
        {
            "id": "btc",
            "title": "Bitcoin",
            "title_en": "Bitcoin",
            "title_ko": "비트코인",
            "value": None if btc_last is None else round(btc_last, 0),
            "unit": "USD",
            "delta": _pct_chg(btc, 20),
            "delta_label": "20d %",
            "delta_label_en": "20d %",
            "delta_label_ko": "20일 %",
            "series": _series_points(btc, n=60),
            "blurb": "BTC-USD spot",
            "blurb_en": "BTC-USD spot",
            "blurb_ko": "BTC-USD 현물",
        },
        {
            "id": "gold",
            "title": "Gold",
            "title_en": "Gold",
            "title_ko": "금",
            "value": None if gold_last is None else round(gold_last, 1),
            "unit": "USD",
            "delta": _pct_chg(gold, 20),
            "delta_label": "20d %",
            "delta_label_en": "20d %",
            "delta_label_ko": "20일 %",
            "series": _series_points(gold, n=60),
            "blurb": "GC=F futures",
            "blurb_en": "GC=F futures",
            "blurb_ko": "GC=F 선물",
        },
        {
            "id": "fear_greed",
            "title": "Equity fear & greed",
            "title_en": "Equity fear & greed",
            "title_ko": "주식 공·탐",
            "value": fg.get("score"),
            "unit": "",
            "delta": None,
            "series": fg.get("series") or [],
            "blurb": fg.get("label_en") or fg.get("label") or "VIX·SPY mood proxy",
            "blurb_en": fg.get("label_en") or "VIX·SPY mood proxy",
            "blurb_ko": fg.get("label_ko") or fg.get("label") or "VIX·SPY 심리 근사",
            "extra": {
                "label": fg.get("label_en") or fg.get("label"),
                "label_en": fg.get("label_en"),
                "label_ko": fg.get("label_ko") or fg.get("label"),
                "vix": (fg.get("components") or {}).get("vix"),
            },
        },
        {
            "id": "crypto_fear_greed",
            "title": "Crypto fear & greed",
            "title_en": "Crypto fear & greed",
            "title_ko": "가상화폐 공·탐",
            "value": cfg.get("score"),
            "unit": "",
            "delta": None,
            "series": cfg.get("series") or [],
            "blurb": cfg.get("label_en") or "Alternative.me",
            "blurb_en": cfg.get("label_en") or "Alternative.me",
            "blurb_ko": cfg.get("label_ko") or "Alternative.me",
            "extra": {
                "label": cfg.get("label_en") or cfg.get("label"),
                "label_en": cfg.get("label_en"),
                "label_ko": cfg.get("label_ko") or cfg.get("label"),
                "source": cfg.get("source"),
                "asof": cfg.get("asof"),
            },
        },
    ]

    # Drop empty cards (e.g. FRED-only series on Yahoo fallback)
    cards = [c for c in cards if c.get("value") is not None or (c.get("series") or [])]

    return {
        "asof": time.strftime("%Y-%m-%d"),
        "source": source,
        "cards": cards,
        "treasury_10y": t10_last,
        "fedwatch": _fedwatch_proxy(fed_last, irx_last),
        "fear_greed": {
            "score": fg.get("score"),
            "label": fg.get("label"),
            "series": fg.get("series") or [],
            "disclaimer": fg.get("disclaimer"),
        },
        "crypto_fear_greed": {
            "score": cfg.get("score"),
            "label": cfg.get("label"),
            "label_en": cfg.get("label_en"),
            "label_ko": cfg.get("label_ko"),
            "series": cfg.get("series") or [],
            "source": cfg.get("source"),
            "disclaimer": cfg.get("disclaimer"),
        },
        "disclaimer": "Educational US macro gauges — not investment advice.",
        "disclaimer_en": "Educational US macro gauges — not investment advice.",
        "disclaimer_ko": "교육·연구용 거시 지표입니다. 투자 권유가 아닙니다.",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def board_cache_status() -> dict[str, Any]:
    """Cached `asof` + age without rebuilding. For the freshness watchdog.

    `get_macro_board()` falls through to `compute_macro_board()` (FRED, yfinance)
    whenever the cache is cold or past TTL — a health endpoint must never be the
    thing that triggers that.
    """
    path = _path()
    if not path.exists():
        return {"present": False, "asof": None, "cache_age_hours": None, "ttl_hours": BOARD_TTL_HOURS}
    age_h = (time.time() - path.stat().st_mtime) / 3600.0
    asof = None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(cached, dict):
            asof = cached.get("asof")
    except Exception:  # noqa: BLE001
        pass
    return {
        "present": True,
        "asof": asof,
        "cache_age_hours": round(age_h, 3),
        "ttl_hours": BOARD_TTL_HOURS,
    }


def get_macro_board(*, force: bool = False) -> dict[str, Any]:
    path = _path()
    if not force and path.exists():
        age_h = (time.time() - path.stat().st_mtime) / 3600.0
        if age_h <= BOARD_TTL_HOURS:
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and cached.get("cards"):
                    cached["cached"] = True
                    return cached
            except Exception:  # noqa: BLE001
                pass
    try:
        payload = compute_macro_board()
        payload["cached"] = False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            from kostolany import blob_cache

            blob_cache.push_async(path)
        except Exception:  # noqa: BLE001
            pass
        return payload
    except Exception as exc:  # noqa: BLE001
        log.exception("macro board failed")
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                cached["stale"] = True
                cached["error"] = str(exc)[:160]
                return cached
            except Exception:  # noqa: BLE001
                pass
        return {
            "asof": time.strftime("%Y-%m-%d"),
            "source": "fallback",
            "cards": [],
            "fedwatch": {"label": "오류", "note": str(exc)[:160]},
            "fear_greed": {},
            "disclaimer": "교육·연구용 거시 지표입니다.",
            "error": str(exc)[:200],
        }
