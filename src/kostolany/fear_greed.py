"""Market fear–greed style gauge for Flows (FedWatch API unavailable).

CME FedWatch JSON is blocked for scraping; we approximate risk appetite from
VIX + SPY momentum + short-rate drift (IRX), which tracks the same macro mood
traders watch alongside FedWatch.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import numpy as np
import pandas as pd

from kostolany.settings import get_settings

log = logging.getLogger(__name__)

GAUGE_TTL_HOURS = 6.0
_CACHE_NAME = "macro_fear_greed_v2.json"
_SERIES_LOOKBACK = 260  # ~1y trading days of score history


def _path():
    return get_settings().cache_path / _CACHE_NAME


def _label(score: float) -> str:
    if score < 20:
        return "극단적 공포"
    if score < 40:
        return "공포"
    if score < 60:
        return "중립"
    if score < 80:
        return "탐욕"
    return "극단적 탐욕"


def _vix_to_score(vix: float) -> float:
    """Map VIX to 0–100 greed (high VIX → fear). Piecewise like desk heuristics."""
    x = float(vix)
    knots_v = np.asarray([10.0, 12.0, 16.0, 20.0, 25.0, 30.0, 40.0, 50.0])
    knots_s = np.asarray([90.0, 82.0, 68.0, 50.0, 35.0, 22.0, 8.0, 3.0])
    return float(np.interp(x, knots_v, knots_s))


def _mom_to_score(ret_20: float) -> float:
    """20d SPY return → 0–100 (≈ ±12% spans the range)."""
    return float(np.clip(50.0 + (ret_20 / 0.12) * 40.0, 5.0, 95.0))


def _rate_to_score(irx_chg_20: float) -> float:
    """Falling bill yields → dovish / risk-on (higher score)."""
    return float(np.clip(50.0 - (irx_chg_20 / 0.50) * 40.0, 5.0, 95.0))


def _score_row(vix: float | None, ret_20: float | None, irx_chg: float | None) -> float:
    parts: list[tuple[float, float]] = []
    if vix is not None and np.isfinite(vix):
        parts.append((_vix_to_score(vix), 0.55))
    if ret_20 is not None and np.isfinite(ret_20):
        parts.append((_mom_to_score(ret_20), 0.30))
    if irx_chg is not None and np.isfinite(irx_chg):
        parts.append((_rate_to_score(irx_chg), 0.15))
    if not parts:
        return 50.0
    wsum = sum(w for _, w in parts)
    return float(np.clip(round(sum(s * w for s, w in parts) / wsum), 0, 100))


def _fetch_panels() -> tuple[pd.Series, pd.Series, pd.Series]:
    import yfinance as yf

    vix = yf.Ticker("^VIX").history(period="18mo", auto_adjust=True)["Close"].astype(float)
    spy = yf.Ticker("SPY").history(period="18mo", auto_adjust=True)["Close"].astype(float)
    irx = yf.Ticker("^IRX").history(period="18mo", auto_adjust=True)["Close"].astype(float)
    for s in (vix, spy, irx):
        s.index = pd.to_datetime(s.index).tz_localize(None)
    return vix.sort_index(), spy.sort_index(), irx.sort_index()


def compute_fear_greed() -> dict[str, Any]:
    vix, spy, irx = _fetch_panels()
    idx = vix.index.intersection(spy.index).intersection(irx.index)
    if len(idx) < 40:
        raise ValueError("Insufficient macro history for fear-greed")

    vix_a = vix.reindex(idx).ffill()
    spy_a = spy.reindex(idx).ffill()
    irx_a = irx.reindex(idx).ffill()
    ret20 = spy_a / spy_a.shift(20) - 1.0
    irx_chg = irx_a - irx_a.shift(20)

    series_rows: list[dict[str, Any]] = []
    usable = idx[20:]  # need 20d lookback
    for ts in usable[-_SERIES_LOOKBACK:]:
        score = _score_row(
            float(vix_a.loc[ts]),
            float(ret20.loc[ts]) if pd.notna(ret20.loc[ts]) else None,
            float(irx_chg.loc[ts]) if pd.notna(irx_chg.loc[ts]) else None,
        )
        series_rows.append({"date": str(pd.Timestamp(ts).date()), "value": score})

    last_ts = usable[-1]
    score = float(series_rows[-1]["value"]) if series_rows else 50.0
    vix_last = float(vix_a.loc[last_ts])
    ret_last = float(ret20.loc[last_ts]) if pd.notna(ret20.loc[last_ts]) else None
    irx_last = float(irx_chg.loc[last_ts]) if pd.notna(irx_chg.loc[last_ts]) else None

    return {
        "score": score,
        "label": _label(score),
        "scale": "fear_greed_0_100",
        "asof": str(pd.Timestamp(last_ts).date()),
        "source": "VIX·SPY·IRX proxy (CME FedWatch API 비연동)",
        "series": series_rows,
        "components": {
            "vix": round(vix_last, 2),
            "spy_ret_20": None if ret_last is None else round(ret_last * 100.0, 2),
            "irx_chg_20_bp": None if irx_last is None else round(irx_last * 100.0, 1),
            "weights": {"vix": 0.55, "momentum": 0.30, "rates": 0.15},
        },
        "disclaimer": "교육용 시장 심리 근사치입니다. CME FedWatch 공식 확률이 아닙니다.",
    }


def get_fear_greed(*, force: bool = False) -> dict[str, Any]:
    path = _path()
    if not force and path.exists():
        age_h = (time.time() - path.stat().st_mtime) / 3600.0
        if age_h <= GAUGE_TTL_HOURS:
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(cached, dict) and "score" in cached:
                    cached["cached"] = True
                    cached["cache_age_hours"] = round(age_h, 3)
                    return cached
            except Exception:  # noqa: BLE001
                pass
    try:
        payload = compute_fear_greed()
        payload["cached"] = False
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            from kostolany import blob_cache

            blob_cache.push_async(path)
        except Exception:  # noqa: BLE001
            pass
        return payload
    except Exception as exc:  # noqa: BLE001
        log.warning("fear-greed compute failed: %s", exc)
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                cached["stale"] = True
                cached["error"] = str(exc)[:160]
                return cached
            except Exception:  # noqa: BLE001
                pass
        return {
            "score": 50,
            "label": "중립",
            "scale": "fear_greed_0_100",
            "asof": time.strftime("%Y-%m-%d"),
            "source": "fallback",
            "series": [],
            "components": {},
            "disclaimer": "교육용 시장 심리 근사치입니다.",
            "error": str(exc)[:160],
        }
