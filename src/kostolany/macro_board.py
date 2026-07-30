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

BOARD_TTL_HOURS = 6.0
_CACHE = "macro_board_v1.json"

# Extra FRED series for the desk (beyond engine feature panel)
BOARD_FRED = {
    "FEDFUNDS": "fed_funds",
    "T10Y2Y": "yield_curve",
    "CPIAUCSL": "cpi",
    "UNRATE": "unemployment",
    "PAYEMS": "payrolls",
    "DGS10": "treasury_10y",
    "DGS2": "treasury_2y",
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


def _yahoo_board(start: str) -> pd.DataFrame:
    import yfinance as yf

    frames: dict[str, pd.Series] = {}
    for t, name in (("^IRX", "fed_funds"), ("^TNX", "treasury_10y"), ("^FVX", "treasury_5y")):
        try:
            hist = yf.Ticker(t).history(start=start, auto_adjust=True)
            if hist.empty:
                continue
            s = hist["Close"].astype(float)
            s.index = pd.to_datetime(s.index).tz_localize(None)
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


def _fedwatch_proxy(fed_funds: float | None, irx: float | None) -> dict[str, Any]:
    """Educational stand-in: bill yield vs fed funds → cut / hold / hike bias.

    Not CME FedWatch official probabilities (API/scrape blocked).
    """
    if fed_funds is None or irx is None or not np.isfinite(fed_funds) or not np.isfinite(irx):
        return {
            "label": "데이터 부족",
            "cut": None,
            "hold": None,
            "hike": None,
            "source": "proxy",
            "note": "CME FedWatch 공식 확률이 아닙니다. 단기금리·기준금리 괴리 근사치입니다.",
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
        label = "인하 쪽 기운"
    elif hike >= hold and hike >= cut:
        label = "인상 쪽 기운"
    else:
        label = "동결 쪽 기운"
    return {
        "label": label,
        "cut": round(cut * 100, 1),
        "hold": round(hold * 100, 1),
        "hike": round(hike * 100, 1),
        "gap_pp": round(gap, 3),
        "source": "IRX vs FEDFUNDS proxy",
        "note": "교육용 근사치입니다. CME FedWatch 공식 확률이 아닙니다.",
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

    cpi_yoy = _yoy(cpi) if not cpi.empty else pd.Series(dtype=float)
    payrolls_chg = payrolls.diff() if not payrolls.empty else pd.Series(dtype=float)

    # Short-rate proxy for FedWatch: prefer IRX from Yahoo if available
    irx_last = None
    try:
        import yfinance as yf

        irx = yf.Ticker("^IRX").history(period="3mo", auto_adjust=True)["Close"].astype(float)
        if not irx.empty:
            irx_last = float(irx.iloc[-1])
    except Exception:  # noqa: BLE001
        irx_last = _last(fed)

    from kostolany.fear_greed import get_fear_greed

    fg = get_fear_greed(force=False)

    fed_last = _last(fed)
    cards = [
        {
            "id": "rates",
            "title": "기준금리",
            "value": fed_last,
            "unit": "%",
            "delta": None
            if fed_last is None or len(fed) < 2
            else round(fed_last - float(fed.iloc[-2]), 3),
            "series": _series_points(fed, n=48),
            "blurb": "FEDFUNDS (또는 단기금리 프록시)",
        },
        {
            "id": "curve",
            "title": "장단기 스프레드",
            "value": _last(curve),
            "unit": "%p",
            "delta": None,
            "series": _series_points(curve, n=48),
            "blurb": "10Y−단기 (T10Y2Y)",
        },
        {
            "id": "cpi",
            "title": "물가 (CPI YoY)",
            "value": _last(cpi_yoy),
            "unit": "%",
            "delta": None,
            "series": _series_points(cpi_yoy.dropna(), n=48),
            "blurb": "소비자물가 전년동월비",
        },
        {
            "id": "jobs",
            "title": "고용 (실업률)",
            "value": _last(unrate),
            "unit": "%",
            "delta": None
            if _last(payrolls_chg) is None
            else round(float(_last(payrolls_chg) or 0), 0),
            "delta_label": "비농 증감(천)",
            "series": _series_points(unrate, n=48),
            "blurb": "UNRATE · PAYEMS 변화",
        },
        {
            "id": "fear_greed",
            "title": "공·탐 지수",
            "value": fg.get("score"),
            "unit": "",
            "delta": None,
            "series": fg.get("series") or [],
            "blurb": fg.get("label") or "시장 심리",
            "extra": {"label": fg.get("label"), "vix": (fg.get("components") or {}).get("vix")},
        },
    ]

    return {
        "asof": time.strftime("%Y-%m-%d"),
        "source": source,
        "cards": cards,
        "treasury_10y": _last(t10),
        "fedwatch": _fedwatch_proxy(fed_last, irx_last),
        "fear_greed": {
            "score": fg.get("score"),
            "label": fg.get("label"),
            "series": fg.get("series") or [],
            "disclaimer": fg.get("disclaimer"),
        },
        "disclaimer": "교육·연구용 거시 지표입니다. 투자 권유가 아닙니다.",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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
