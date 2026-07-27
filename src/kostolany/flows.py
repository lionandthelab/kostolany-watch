"""Sector macro Up/Down flows: historical closes + 3-AI forward paths."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd

from kostolany.engine import KostolanyEngine
from kostolany.regimes import Regime
from kostolany.settings import get_settings

FLOWS_TTL_HOURS = 6.0
FORECAST_DAYS = 63  # ~3 months trading days
HISTORY_DAYS = 252  # ~1y

_refresh_lock = threading.Lock()
_refreshing: set[str] = set()
_warmup_state: dict[str, Any] = {
    "running": False,
    "done": 0,
    "total": 0,
    "current": None,
    "started_at": None,
    "finished_at": None,
    "errors": [],
}

# Expected daily drift by Kostolany regime (educational prior, not advice)
REGIME_DAILY_DRIFT: dict[int, float] = {
    int(Regime.A1): 0.00035,
    int(Regime.A2): 0.00055,
    int(Regime.A3): 0.00015,
    int(Regime.B1): -0.00025,
    int(Regime.B2): -0.00055,
    int(Regime.B3): -0.00035,
}

REGIME_DAILY_VOL: dict[int, float] = {
    int(Regime.A1): 0.008,
    int(Regime.A2): 0.010,
    int(Regime.A3): 0.016,
    int(Regime.B1): 0.011,
    int(Regime.B2): 0.014,
    int(Regime.B3): 0.020,
}

# Representative ETFs / indexes (Yahoo symbols). Groups drive Flows UI chips.
SECTORS: list[dict[str, str]] = [
    # Markets / countries
    {"id": "kospi", "label": "코스피", "symbol": "KS11", "blurb": "KS11"},
    {"id": "korea", "label": "한국", "symbol": "EWY", "blurb": "EWY"},
    {"id": "spx", "label": "S&P 500", "symbol": "SPY", "blurb": "SPY"},
    {"id": "japan", "label": "일본", "symbol": "EWJ", "blurb": "EWJ"},
    {"id": "china", "label": "중국", "symbol": "FXI", "blurb": "FXI"},
    {"id": "germany", "label": "독일", "symbol": "EWG", "blurb": "EWG"},
    {"id": "uk", "label": "영국", "symbol": "EWU", "blurb": "EWU"},
    {"id": "india", "label": "인도", "symbol": "INDA", "blurb": "INDA"},
    {"id": "em", "label": "신흥국", "symbol": "EEM", "blurb": "EEM"},
    # US GICS sector SPDRs + bio
    {"id": "tech", "label": "기술", "symbol": "XLK", "blurb": "XLK"},
    {"id": "finance", "label": "금융", "symbol": "XLF", "blurb": "XLF"},
    {"id": "health", "label": "헬스케어", "symbol": "XLV", "blurb": "XLV"},
    {"id": "biotech", "label": "바이오", "symbol": "XBI", "blurb": "XBI"},
    {"id": "energy", "label": "에너지", "symbol": "XLE", "blurb": "XLE"},
    {"id": "materials", "label": "소재", "symbol": "XLB", "blurb": "XLB"},
    {"id": "industry", "label": "산업재", "symbol": "XLI", "blurb": "XLI"},
    {"id": "utilities", "label": "유틸리티", "symbol": "XLU", "blurb": "XLU"},
    {"id": "reit", "label": "리츠", "symbol": "XLRE", "blurb": "XLRE"},
    {"id": "consumer_disc", "label": "임의소비", "symbol": "XLY", "blurb": "XLY"},
    {"id": "consumer_stap", "label": "필수소비", "symbol": "XLP", "blurb": "XLP"},
    {"id": "comms", "label": "커뮤니케이션", "symbol": "XLC", "blurb": "XLC"},
    # Commodities / rates / crypto
    {"id": "gold", "label": "금", "symbol": "GLD", "blurb": "GLD"},
    {"id": "silver", "label": "은", "symbol": "SLV", "blurb": "SLV"},
    {"id": "oil", "label": "원유", "symbol": "USO", "blurb": "USO"},
    {"id": "pdbc", "label": "원자재복합", "symbol": "PDBC", "blurb": "PDBC"},
    {"id": "tlt", "label": "장기국채", "symbol": "TLT", "blurb": "TLT"},
    {"id": "hyg", "label": "하이일드", "symbol": "HYG", "blurb": "HYG"},
    {"id": "btc", "label": "BTC", "symbol": "BTC-USD", "blurb": "BTC-USD"},
]

SECTOR_GROUPS: list[dict[str, Any]] = [
    {
        "id": "markets",
        "label_ko": "시장·국가",
        "sector_ids": [
            "kospi",
            "korea",
            "spx",
            "japan",
            "china",
            "germany",
            "uk",
            "india",
            "em",
        ],
    },
    {
        "id": "us_sectors",
        "label_ko": "미국 섹터",
        "sector_ids": [
            "tech",
            "finance",
            "health",
            "biotech",
            "energy",
            "materials",
            "industry",
            "utilities",
            "reit",
            "consumer_disc",
            "consumer_stap",
            "comms",
        ],
    },
    {
        "id": "cmdty",
        "label_ko": "원자재",
        "sector_ids": ["gold", "silver", "oil", "pdbc"],
    },
    {
        "id": "rates_crypto",
        "label_ko": "금리·가상",
        "sector_ids": ["tlt", "hyg", "btc"],
    },
]

ANALYSTS = [
    {"id": "hmm", "label": "리듬이", "color": "#2f5d50"},
    {"id": "gbm", "label": "눈치왕", "color": "#c45c3e"},
    {"id": "tsfm", "label": "파도꾼", "color": "#4a7c9b"},
]


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or time.time(), tz=timezone.utc).isoformat()


def _cache_path(sector_id: str):
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in sector_id)
    return get_settings().cache_path / f"flows_{safe}.json"


def list_sectors() -> dict[str, Any]:
    sectors = [
        {"id": s["id"], "label": s["label"], "blurb": s["blurb"], "symbol": s["symbol"]}
        for s in SECTORS
    ]
    return {"sectors": sectors, "groups": SECTOR_GROUPS}


def _sector_meta(sector_id: str) -> dict[str, str]:
    alias = {"commodities": "pdbc"}.get(sector_id, sector_id)
    for s in SECTORS:
        if s["id"] == alias:
            return s
    raise ValueError(f"Unknown sector: {sector_id}")


def read_flow_cache(sector_id: str) -> dict[str, Any] | None:
    """Return disk cache at any age (stale-while-revalidate)."""
    path = _cache_path(sector_id)
    if not path.exists():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(cached, dict) or not cached.get("history"):
        return None
    age_h = (time.time() - path.stat().st_mtime) / 3600.0
    out = dict(cached)
    out["cached"] = True
    out["stale"] = age_h > FLOWS_TTL_HOURS
    out["cache_age_hours"] = round(age_h, 3)
    out["refreshing"] = sector_id in _refreshing
    return out


def warmup_status() -> dict[str, Any]:
    with _refresh_lock:
        total = int(_warmup_state.get("total") or 0) or len(SECTORS)
        return {
            **_warmup_state,
            "total": total,
            "refreshing": sorted(_refreshing),
            "cached_sectors": [s["id"] for s in SECTORS if _cache_path(s["id"]).exists()],
        }


def _schedule_rebuild(sector_id: str) -> bool:
    """Start background rebuild if not already running. Returns True if started."""
    with _refresh_lock:
        if sector_id in _refreshing:
            return False
        _refreshing.add(sector_id)

    def _run() -> None:
        try:
            _compute_sector_flow(sector_id)
        except Exception as exc:  # noqa: BLE001
            with _refresh_lock:
                errs = _warmup_state.setdefault("errors", [])
                if isinstance(errs, list):
                    errs.append({"sector": sector_id, "error": str(exc)[:200]})
                    _warmup_state["errors"] = errs[-20:]
        finally:
            with _refresh_lock:
                _refreshing.discard(sector_id)

    threading.Thread(target=_run, name=f"flow-refresh-{sector_id}", daemon=True).start()
    return True


def warmup_all_flows(*, force: bool = False) -> dict[str, Any]:
    """Precompute every sector (skip fresh cache unless force). Non-blocking kick."""
    with _refresh_lock:
        if _warmup_state.get("running"):
            return warmup_status()
        _warmup_state.update(
            {
                "running": True,
                "done": 0,
                "total": len(SECTORS),
                "current": None,
                "started_at": _iso(),
                "finished_at": None,
                "errors": [],
            }
        )

    def _run() -> None:
        try:
            for s in SECTORS:
                sid = s["id"]
                with _refresh_lock:
                    _warmup_state["current"] = sid
                cached = read_flow_cache(sid)
                if cached is not None and not cached.get("stale") and not force:
                    with _refresh_lock:
                        _warmup_state["done"] = int(_warmup_state.get("done") or 0) + 1
                    continue
                while True:
                    with _refresh_lock:
                        busy = sid in _refreshing
                    if not busy:
                        break
                    time.sleep(0.4)
                with _refresh_lock:
                    _refreshing.add(sid)
                try:
                    _compute_sector_flow(sid)
                except Exception as exc:  # noqa: BLE001
                    with _refresh_lock:
                        errs = _warmup_state.setdefault("errors", [])
                        if isinstance(errs, list):
                            errs.append({"sector": sid, "error": str(exc)[:200]})
                finally:
                    with _refresh_lock:
                        _refreshing.discard(sid)
                        _warmup_state["done"] = int(_warmup_state.get("done") or 0) + 1
        finally:
            with _refresh_lock:
                _warmup_state["running"] = False
                _warmup_state["current"] = None
                _warmup_state["finished_at"] = _iso()

    threading.Thread(target=_run, name="flow-warmup", daemon=True).start()
    return warmup_status()


def _blend_drift(proba: dict[str, float]) -> tuple[float, float]:
    drift = 0.0
    vol = 0.0
    mass = 0.0
    for name, p in proba.items():
        if name not in Regime.__members__:
            continue
        rid = int(Regime[name])
        w = max(0.0, float(p))
        drift += w * REGIME_DAILY_DRIFT[rid]
        vol += w * REGIME_DAILY_VOL[rid]
        mass += w
    if mass < 1e-9:
        return 0.0, 0.012
    return drift / mass, max(0.004, vol / mass)


def _forward_dates(last: pd.Timestamp, n: int) -> list[str]:
    idx = pd.bdate_range(start=last + pd.Timedelta(days=1), periods=n)
    return [str(ts.date()) for ts in idx]


def _path_from_proba(
    last_close: float,
    proba: dict[str, float],
    dates: list[str],
    *,
    seed: int,
    stickiness: float,
    shock: float,
) -> list[dict[str, float | str]]:
    """Simulate forward index level from regime-implied drift (deterministic RNG)."""
    rng = np.random.default_rng(seed)
    drift0, vol0 = _blend_drift(proba)
    level = float(last_close)
    out: list[dict[str, float | str]] = []
    # Mild mean-reversion of drift toward 0 over the horizon
    for i, d in enumerate(dates):
        t = i / max(1, len(dates) - 1)
        drift = drift0 * (stickiness + (1 - stickiness) * (1 - t))
        # Model personality: shock scales residual noise (not market truth)
        eps = float(rng.normal(0.0, vol0 * shock))
        level = level * (1.0 + drift + eps)
        out.append({"date": d, "value": round(level, 6)})
    return out


def _tsfm_path(
    eng: KostolanyEngine,
    last_close: float,
    dates: list[str],
    proba: dict[str, float],
) -> list[dict[str, float | str]]:
    """Prefer TSFM multi-horizon signal; fall back to regime path."""
    drift0, vol0 = _blend_drift(proba)
    level = float(last_close)
    # Pull horizon hints from last trajectory if present
    h1 = h5 = h20 = 0.0
    traj = getattr(eng.model, "last_traj_", None)
    if traj is not None and hasattr(traj, "ret_hat") and len(traj.ret_hat):
        row = traj.ret_hat.iloc[-1]
        h1 = float(row.get("h1", 0.0) or 0.0)
        h5 = float(row.get("h5", 0.0) or 0.0)
        h20 = float(row.get("h20", 0.0) or 0.0)
    # Map horizon returns into a decaying daily drift blend
    daily_from_tsfm = 0.45 * (h1 / 1.0) + 0.35 * (h5 / 5.0) + 0.20 * (h20 / 20.0)
    # Tuned (KS11 WF): milder scale + less TSFM weight vs regime prior → lower MAE
    daily_from_tsfm = float(np.clip(daily_from_tsfm * 0.017, -0.0030, 0.0030))
    out: list[dict[str, float | str]] = []
    rng = np.random.default_rng(77)
    for i, d in enumerate(dates):
        t = i / max(1, len(dates) - 1)
        drift = (0.45 * daily_from_tsfm + 0.55 * drift0) * (0.9 + 0.1 * (1 - t))
        level = level * (1.0 + drift + float(rng.normal(0.0, vol0 * 0.30)))
        out.append({"date": d, "value": round(level, 6)})
    return out


def build_sector_flow(
    sector_id: str,
    *,
    use_cache: bool = True,
    refresh: bool = False,
) -> dict[str, Any]:
    """Serve cache immediately; refresh only in background when possible.

    - Fresh cache → return
    - Stale / refresh requested + cache exists → return stale, rebuild in bg
    - Cold miss → compute synchronously (unavoidable once)
    """
    _sector_meta(sector_id)  # validate
    cached = read_flow_cache(sector_id)

    if refresh:
        if cached is not None:
            started = _schedule_rebuild(sector_id)
            out = dict(cached)
            out["refreshing"] = True
            out["refresh_started"] = started
            return out
        # no cache — must compute now
        return _compute_sector_flow(sector_id)

    if use_cache and cached is not None:
        if cached.get("stale"):
            _schedule_rebuild(sector_id)
            cached = read_flow_cache(sector_id) or cached
            cached["refreshing"] = True
        return cached

    return _compute_sector_flow(sector_id)


def _compute_sector_flow(sector_id: str) -> dict[str, Any]:
    meta = _sector_meta(sector_id)
    path = _cache_path(sector_id)

    # Fit three analysts on the sector symbol
    snaps: dict[str, Any] = {}
    engines: dict[str, KostolanyEngine] = {}
    for a in ANALYSTS:
        eng = KostolanyEngine(model_kind=a["id"])  # type: ignore[arg-type]
        eng.fit_symbol(meta["symbol"])
        engines[a["id"]] = eng
        snap = eng.snapshot()
        snaps[a["id"]] = {
            "regime": snap.regime,
            "confidence": snap.confidence,
            "probabilities": snap.probabilities,
            "asof": snap.asof,
        }

    ohlcv = engines["hmm"]._last_ohlcv
    assert ohlcv is not None
    closes = ohlcv["close"].dropna().astype(float).sort_index().tail(HISTORY_DAYS)
    last_close = float(closes.iloc[-1])
    last_ts = pd.Timestamp(closes.index[-1])
    # Normalize history to 100 at last point for Up/Down readability
    hist_norm = (closes / last_close) * 100.0
    history = [
        {"date": str(pd.Timestamp(ts).date()), "value": round(float(v), 4)}
        for ts, v in hist_norm.items()
    ]

    fwd_dates = _forward_dates(last_ts, FORECAST_DAYS)
    forecasts = []
    for a in ANALYSTS:
        proba = snaps[a["id"]]["probabilities"]
        if a["id"] == "hmm":
            series = _path_from_proba(100.0, proba, fwd_dates, seed=11, stickiness=0.92, shock=0.35)
        elif a["id"] == "gbm":
            series = _path_from_proba(100.0, proba, fwd_dates, seed=29, stickiness=0.72, shock=0.85)
        else:
            series = _tsfm_path(engines["tsfm"], 100.0, fwd_dates, proba)
        end = float(series[-1]["value"]) if series else 100.0
        forecasts.append(
            {
                "id": a["id"],
                "label": a["label"],
                "color": a["color"],
                "regime": snaps[a["id"]]["regime"],
                "confidence": snaps[a["id"]]["confidence"],
                "outlook": "up" if end >= 100.0 else "down",
                "change_pct": round((end / 100.0 - 1.0) * 100.0, 2),
                "points": series,
            }
        )

    # Consensus: average of three terminal levels
    term = [float(f["points"][-1]["value"]) for f in forecasts if f["points"]]
    consensus = float(np.mean(term)) if term else 100.0

    payload = {
        "sector": {
            "id": meta["id"],
            "label": meta["label"],
            "symbol": meta["symbol"],
            "blurb": meta["blurb"],
        },
        "asof": snaps["hmm"]["asof"],
        "cached": False,
        "stale": False,
        "refreshing": False,
        "ttl_hours": FLOWS_TTL_HOURS,
        "history": history,
        "forecasts": forecasts,
        "consensus": {
            "change_pct": round((consensus / 100.0 - 1.0) * 100.0, 2),
            "outlook": "up" if consensus >= 100.0 else "down",
        },
        "disclaimer": (
            "실데이터(지수=100) + AI 3명 3개월 시나리오. 투자 권유 아님."
        ),
        "built_at": _iso(),
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return payload
