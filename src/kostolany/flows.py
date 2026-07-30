"""Sector macro Up/Down flows: historical closes + 3-AI forward paths."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, NamedTuple

import numpy as np
import pandas as pd

from kostolany.engine import KostolanyEngine, fit_analyst_bundle
from kostolany.regimes import Regime
from kostolany.settings import get_settings

FLOWS_TTL_HOURS = 6.0
FORECAST_DAYS = 63  # ~3 months trading days
HISTORY_DAYS = 252  # ~1y (default full-flow payload)

# Visible history windows for the Flows chart range badges.
HIST_RANGES: dict[str, dict[str, Any]] = {
    "6m": {"months": 6, "label_ko": "6개월"},
    "1y": {"months": 12, "label_ko": "1년"},
    "3y": {"months": 36, "label_ko": "3년"},
    "5y": {"months": 60, "label_ko": "5년"},
}
DEFAULT_HIST_RANGE = "1y"

_refresh_lock = threading.Lock()
_refreshing: set[str] = set()
_job_lock = threading.Lock()
_priority_q: deque[str] = deque()
_normal_q: deque[str] = deque()
_queued: set[str] = set()
_worker_running = False
_warmup_state: dict[str, Any] = {
    "running": False,
    "done": 0,
    "total": 0,
    "current": None,
    "started_at": None,
    "finished_at": None,
    "errors": [],
}

# Per-regime daily drift, FITTED (not hand-set) from conditional 63d forward
# returns with a leg-block bootstrap CI on SPY and BTC-USD
# (artifacts/experiments/regime_drift_fit_20260730.json). Pre-committed rule:
# a class keeps a drift ONLY if its CI excludes zero with the SAME sign on
# both reference markets, and then takes the smaller magnitude. Measured
# result: NO class has significant negative drift anywhere — B1/B2/B3 were
# followed by flat-to-POSITIVE returns — so the down-leg classes are 0.0 and
# a prior-driven arm can no longer claim downside it never predicted.
REGIME_DAILY_DRIFT: dict[int, float] = {
    int(Regime.A1): 0.00090,
    int(Regime.A2): 0.00035,
    int(Regime.A3): 0.00049,
    int(Regime.B1): 0.0,
    int(Regime.B2): 0.0,
    int(Regime.B3): 0.0,
}

# Outlook calls below this |terminal change| are rendered "flat": the shipped
# consensus was measured ANTI-informative on direction (P-CONS-1: SPY skill
# -0.327), so a hair-thin tilt must not display as an up/down call.
FLAT_OUTLOOK_PCT = 0.75


def _outlook(change_pct: float) -> str:
    if change_pct >= FLAT_OUTLOOK_PCT:
        return "up"
    if change_pct <= -FLAT_OUTLOOK_PCT:
        return "down"
    return "flat"

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
    # Markets / countries (KS11/kospi retired 2026-07-30 — owner decision;
    # the KRX connector stays as a research data lane only)
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
    # Suffix bumps when the Flows forecast head or payload contract changes.
    # v2: band / p_up / arm_kind + honest disclaimer — old blobs would keep
    # serving the "AI 3명" framing and no uncertainty, so they must not be read.
    return get_settings().cache_path / f"flows_{safe}_pooled_v2.json"


def _hist_cache_path(sector_id: str, range_key: str = DEFAULT_HIST_RANGE):
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in sector_id)
    rk = range_key if range_key in HIST_RANGES else DEFAULT_HIST_RANGE
    return get_settings().cache_path / f"flows_{safe}_hist_{rk}_v2.json"


def _closes_cache_path(sector_id: str):
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in sector_id)
    return get_settings().cache_path / f"flows_{safe}_closes_v2.json"


HIST_TTL_HOURS = 12.0


def _normalize_history(
    closes: pd.Series,
    *,
    max_bars: int | None = None,
) -> tuple[list[dict[str, float | str]], str]:
    closes = closes.dropna().astype(float).sort_index()
    if max_bars is not None:
        closes = closes.tail(max_bars)
    if closes.empty:
        raise ValueError("No close prices")
    last_close = float(closes.iloc[-1])
    last_ts = pd.Timestamp(closes.index[-1])
    hist_norm = (closes / last_close) * 100.0
    history = [
        {"date": str(pd.Timestamp(ts).date()), "value": round(float(v), 4)}
        for ts, v in hist_norm.items()
    ]
    return history, str(last_ts.date())


def _load_closes(sector_id: str, *, force: bool = False) -> pd.Series:
    """Load ~5.5y closes once; reuse across range badges."""
    from kostolany import blob_cache
    from kostolany.connectors import load_market

    meta = _sector_meta(sector_id)
    path = _closes_cache_path(sector_id)
    if not force:
        blob_cache.pull_if_missing(path)
        if path.exists():
            age_h = (time.time() - path.stat().st_mtime) / 3600.0
            if age_h <= HIST_TTL_HOURS:
                try:
                    blob = json.loads(path.read_text(encoding="utf-8"))
                    idx = pd.to_datetime(blob["dates"])
                    return pd.Series(blob["closes"], index=idx, dtype=float).sort_index()
                except Exception:  # noqa: BLE001
                    pass

    start = (pd.Timestamp.utcnow().normalize() - pd.DateOffset(months=66)).strftime("%Y-%m-%d")
    market = load_market(meta["symbol"], start=start, enrich_fred=False)
    closes = market.ohlcv["close"].dropna().astype(float).sort_index()
    try:
        payload = {
            "dates": [str(pd.Timestamp(ts).date()) for ts in closes.index],
            "closes": [float(v) for v in closes.to_numpy()],
            "symbol": meta["symbol"],
            "saved_at": _iso(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        blob_cache.push_async(path)
    except Exception:  # noqa: BLE001
        pass
    return closes


def read_history_cache(sector_id: str, range_key: str = DEFAULT_HIST_RANGE) -> dict[str, Any] | None:
    from kostolany import blob_cache

    path = _hist_cache_path(sector_id, range_key)
    blob_cache.pull_if_missing(path)
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
    out["stale"] = age_h > HIST_TTL_HOURS
    out["cache_age_hours"] = round(age_h, 3)
    return out


def build_sector_history(
    sector_id: str,
    *,
    range_key: str = DEFAULT_HIST_RANGE,
    force: bool = False,
) -> dict[str, Any]:
    """Fast OHLCV-only payload so the UI can paint today without waiting on AI."""
    meta = _sector_meta(sector_id)
    rk = range_key if range_key in HIST_RANGES else DEFAULT_HIST_RANGE
    if not force:
        cached = read_history_cache(sector_id, rk)
        if cached is not None and not cached.get("stale"):
            return cached

    closes = _load_closes(sector_id, force=force)
    months = int(HIST_RANGES[rk]["months"])
    last_ts = pd.Timestamp(closes.index[-1])
    cutoff = last_ts - pd.DateOffset(months=months)
    window = closes.loc[closes.index >= cutoff]
    if len(window) < 20:
        window = closes.tail(max(20, min(len(closes), HISTORY_DAYS)))
    history, asof = _normalize_history(window)
    payload = {
        "sector": {
            "id": meta["id"],
            "label": meta["label"],
            "symbol": meta["symbol"],
            "blurb": meta["blurb"],
        },
        "asof": asof,
        "history": history,
        "hist_range": rk,
        "hist_range_label": HIST_RANGES[rk]["label_ko"],
        "forecasts": [],
        "consensus": None,
        "forecast_pending": True,
        "cached": False,
        "stale": False,
        "disclaimer": "실데이터(지수=100). 3개월 시나리오는 별도 로딩.",
        "built_at": _iso(),
    }
    path = _hist_cache_path(sector_id, rk)
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        from kostolany import blob_cache

        blob_cache.push_async(path)
    except Exception:  # noqa: BLE001
        pass
    return payload


def list_sectors() -> dict[str, Any]:
    sectors = [
        {"id": s["id"], "label": s["label"], "blurb": s["blurb"], "symbol": s["symbol"]}
        for s in SECTORS
    ]
    return {"sectors": sectors, "groups": SECTOR_GROUPS}


def _sector_meta(sector_id: str) -> dict[str, str]:
    alias = {"commodities": "pdbc", "korea": "spx", "kospi": "spx"}.get(sector_id, sector_id)
    for s in SECTORS:
        if s["id"] == alias:
            return s
    raise ValueError(f"Unknown sector: {sector_id}")


def read_flow_cache(sector_id: str) -> dict[str, Any] | None:
    """Return disk cache at any age (stale-while-revalidate)."""
    from kostolany import blob_cache

    path = _cache_path(sector_id)
    blob_cache.pull_if_missing(path)
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
            "queued": sorted(_queued),
        }


def _cached_count() -> int:
    return sum(1 for s in SECTORS if _cache_path(s["id"]).exists())


def _enqueue(sector_id: str, *, priority: bool) -> bool:
    """Add sector to serial compute queue. Priority jumps the line. Returns True if newly queued."""
    global _worker_running
    with _job_lock:
        if sector_id in _queued:
            # Promote: move from normal → priority if requested
            if priority:
                try:
                    _normal_q.remove(sector_id)
                except ValueError:
                    pass
                if sector_id not in _priority_q:
                    _priority_q.appendleft(sector_id)
            if not _worker_running:
                _worker_running = True
                threading.Thread(target=_flow_worker, name="flow-worker", daemon=True).start()
            return False
        _queued.add(sector_id)
        if priority:
            _priority_q.appendleft(sector_id)
        else:
            _normal_q.append(sector_id)
        if not _worker_running:
            _worker_running = True
            threading.Thread(target=_flow_worker, name="flow-worker", daemon=True).start()
        return True


def _flow_worker() -> None:
    """Single worker: always drain priority queue before normal (one compute at a time)."""
    global _worker_running
    with _job_lock:
        if _warmup_state.get("started_at") is None:
            _warmup_state["started_at"] = _iso()
        _warmup_state["running"] = True
        _warmup_state["total"] = len(SECTORS)

    while True:
        with _job_lock:
            sid: str | None = None
            if _priority_q:
                sid = _priority_q.popleft()
            elif _normal_q:
                sid = _normal_q.popleft()
            if sid is None:
                _worker_running = False
                _warmup_state["running"] = False
                _warmup_state["current"] = None
                _warmup_state["finished_at"] = _iso()
                _warmup_state["done"] = _cached_count()
                return
            _warmup_state["current"] = sid
            _warmup_state["running"] = True

        with _refresh_lock:
            _refreshing.add(sid)
        try:
            _compute_sector_flow(sid)
        except Exception as exc:  # noqa: BLE001
            with _refresh_lock:
                errs = _warmup_state.setdefault("errors", [])
                if isinstance(errs, list):
                    errs.append({"sector": sid, "error": str(exc)[:200]})
                    _warmup_state["errors"] = errs[-20:]
        finally:
            with _refresh_lock:
                _refreshing.discard(sid)
            with _job_lock:
                _queued.discard(sid)
                _warmup_state["done"] = _cached_count()


def _schedule_rebuild(sector_id: str) -> bool:
    """Queue background rebuild (priority). Returns True if newly queued."""
    return _enqueue(sector_id, priority=True)


def ensure_sector(sector_id: str, *, force: bool = False) -> dict[str, Any]:
    """Prioritize building this sector so UI selection is not stuck behind full warmup."""
    _sector_meta(sector_id)
    cached = read_flow_cache(sector_id)
    if cached is not None and not force and not cached.get("stale"):
        return {
            "sector": sector_id,
            "queued": False,
            "has_cache": True,
            "status": warmup_status(),
        }
    queued_new = _enqueue(sector_id, priority=True)
    return {
        "sector": sector_id,
        "queued": True,
        "queued_new": queued_new,
        "has_cache": cached is not None,
        "status": warmup_status(),
    }


def warmup_all_flows(*, force: bool = False) -> dict[str, Any]:
    """Enqueue every sector at low priority (user ensure_sector jumps ahead)."""
    # Fit pooled head once before sector jobs so the first equity path is warm.
    try:
        from kostolany.pooled_serve import warmup_pooled

        threading.Thread(
            target=lambda: warmup_pooled(force=force),
            name="pooled-warmup",
            daemon=True,
        ).start()
    except Exception:  # noqa: BLE001
        pass
    with _job_lock:
        _warmup_state.update(
            {
                "running": True,
                "done": _cached_count(),
                "total": len(SECTORS),
                "started_at": _warmup_state.get("started_at") or _iso(),
                "finished_at": None,
            }
        )
    for s in SECTORS:
        sid = s["id"]
        cached = read_flow_cache(sid)
        if cached is not None and not cached.get("stale") and not force:
            continue
        _enqueue(sid, priority=False)
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


class ArmPath(NamedTuple):
    """One analyst arm's forward path plus whatever uncertainty it actually produced.

    ``band``/``p_up`` stay ``None`` unless the underlying model emitted them —
    a closed-form regime-prior scenario has no predictive distribution to show,
    and inventing one would misrepresent a constant table as a forecast.
    """

    points: list[dict[str, float | str]]
    engine: str
    kind: str  # "regime_prior" (closed-form prior scenario) | "learned"
    band: dict[str, float] | None
    p_up: float | None


# Same clamp the level anchors use, so a band can never render off-chart.
_RET_CLIP = (-0.85, 2.0)


def _band_levels(base: float, lo_ret: float, hi_ret: float) -> dict[str, float] | None:
    """Convert cumulative-return quantiles into 100-based index levels."""
    if not (np.isfinite(lo_ret) and np.isfinite(hi_ret)):
        return None
    lo, hi = (lo_ret, hi_ret) if lo_ret <= hi_ret else (hi_ret, lo_ret)
    lo = float(np.clip(lo, *_RET_CLIP))
    hi = float(np.clip(hi, *_RET_CLIP))
    return {
        "q10": round(float(base) * (1.0 + lo), 6),
        "q90": round(float(base) * (1.0 + hi), 6),
    }


def _finite_or_none(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if np.isfinite(out) else None


def _up_rate(closes: pd.Series, horizon: int = FORECAST_DAYS) -> tuple[float | None, int]:
    """Unconditional share of positive ``horizon``-bar forward returns.

    Purely descriptive: this is the base rate the model's P(up) must be read
    against. It is never a feature or a training target, and the windows
    overlap, so ``n`` is a bar count and not an independent sample count.
    """
    s = closes.dropna().astype(float).sort_index()
    if len(s) <= horizon + 1:
        return None, 0
    fwd = (s.shift(-horizon) / s - 1.0).dropna()
    if fwd.empty:
        return None, 0
    return float((fwd > 0.0).mean()), int(len(fwd))


def _pooled_eligible(symbol: str) -> bool:
    """Equity-only promotion: commodities/bonds/crypto keep LocalTSFM."""
    from kostolany.harness.pooled_forecast import _asset_group

    equity, commodity, bond, crypto = _asset_group(symbol)
    return equity > 0.5 and commodity < 0.5 and bond < 0.5 and crypto < 0.5


def _pooled_path(
    symbol: str,
    last_close: float,
    dates: list[str],
    proba: dict[str, float],
    *,
    direct_weight: float = 0.92,
) -> ArmPath | None:
    """Build path from promoted pooled h63 forecast (falls back to None)."""
    if not _pooled_eligible(symbol):
        return None
    try:
        from kostolany.pooled_serve import forecast_symbol

        fc = forecast_symbol(symbol)
        h63 = float(fc["h63"])
    except Exception:  # noqa: BLE001
        return None
    if not np.isfinite(h63):
        return None
    q10 = _finite_or_none(fc.get("q10"))
    q90 = _finite_or_none(fc.get("q90"))
    p_up = _finite_or_none(fc.get("p_up"))

    drift0, _vol0 = _blend_drift(proba)
    prior_ret = float(np.expm1(np.clip(drift0 * len(dates), -0.8, 0.8)))
    w = float(np.clip(direct_weight, 0.0, 1.0))
    terminal = w * h63 + (1.0 - w) * prior_ret
    mid = w * h63 * (20 / 63) + (1.0 - w) * prior_ret * (20 / 63)
    short = w * h63 * (5 / 63) + (1.0 - w) * prior_ret * (5 / 63)
    anchors_h = np.asarray([0.0, 5.0, 20.0, float(len(dates))])
    anchors_r = np.clip(np.asarray([0.0, short, mid, terminal], dtype=float), *_RET_CLIP)
    anchors_log = np.log1p(anchors_r)
    out: list[dict[str, float | str]] = []
    for i, d in enumerate(dates, start=1):
        cum_log = float(np.interp(i, anchors_h, anchors_log))
        level = float(last_close) * float(np.exp(cum_log))
        out.append({"date": d, "value": round(level, 6)})

    # The plotted centre is shrunk toward the regime prior, so the reported
    # band takes the same affine map; otherwise the cone would not contain its
    # own median. No band is synthesised when the model emitted no quantiles.
    band = (
        _band_levels(last_close, w * q10 + (1.0 - w) * prior_ret, w * q90 + (1.0 - w) * prior_ret)
        if q10 is not None and q90 is not None
        else None
    )
    return ArmPath(points=out, engine="pooled_v1", kind="learned", band=band, p_up=p_up)


def arm_from_trajectory(
    traj: Any,
    last_close: float,
    dates: list[str],
    proba: dict[str, float],
    *,
    direct_weight: float = 0.85,
) -> ArmPath:
    """Pure ArmPath construction from a LocalTSFM trajectory.

    Shared by serving (`_tsfm_path`) and the harness
    (`commercial_eval.evaluate_consensus_market`) so the number that is scored
    is arithmetically the number that ships. Do not fork this logic.
    """
    drift0, vol0 = _blend_drift(proba)
    del vol0
    h5 = h20 = h63 = 0.0
    if traj is not None and hasattr(traj, "ret_hat") and len(traj.ret_hat):
        row = traj.ret_hat.iloc[-1]
        h5 = float(row.get("h5", 0.0) or 0.0)
        h20 = float(row.get("h20", 0.0) or 0.0)
        h63 = float(row.get("h63", 0.0) or 0.0)

    if not np.isfinite(h63) or abs(h63) < 1e-12:
        # Backward-compatible fallback for old/failed model artifacts. This
        # path is the hardcoded regime-prior table, so it must NOT report
        # itself as a learned LocalTSFM forecast.
        return ArmPath(
            points=_path_from_proba(
                last_close,
                proba,
                dates,
                seed=77,
                stickiness=0.82,
                shock=0.0,
            ),
            engine="regime_prior_fallback",
            kind="regime_prior",
            band=None,
            p_up=None,
        )

    # Shrink direct forecasts toward the analyst-specific regime prior.
    prior_ret = float(np.expm1(np.clip(drift0 * len(dates), -0.8, 0.8)))
    direct_weight = float(np.clip(direct_weight, 0.0, 1.0))
    terminal = direct_weight * h63 + (1.0 - direct_weight) * prior_ret
    mid = direct_weight * h20 + (1.0 - direct_weight) * prior_ret * (20 / 63)
    short = direct_weight * h5 + (1.0 - direct_weight) * prior_ret * (5 / 63)

    # Enforce finite, plausible cumulative-return anchors. The caps are broad
    # enough for crypto while preventing malformed models from breaking SVGs.
    anchors_h = np.asarray([0.0, 5.0, 20.0, float(len(dates))])
    anchors_r = np.clip(
        np.asarray([0.0, short, mid, terminal], dtype=float), *_RET_CLIP
    )
    anchors_log = np.log1p(anchors_r)
    out: list[dict[str, float | str]] = []
    for i, d in enumerate(dates, start=1):
        cum_log = float(np.interp(i, anchors_h, anchors_log))
        level = float(last_close) * float(np.exp(cum_log))
        out.append({"date": d, "value": round(level, 6)})

    # Same shrinkage the median got; absent quantile heads stay absent.
    q10 = q90 = None
    quants = getattr(traj, "quantiles", None)
    if quants is not None and len(quants):
        qrow = quants.iloc[-1]
        q10 = _finite_or_none(qrow.get("h63_q10"))
        q90 = _finite_or_none(qrow.get("h63_q90"))
    band = (
        _band_levels(
            last_close,
            direct_weight * q10 + (1.0 - direct_weight) * prior_ret,
            direct_weight * q90 + (1.0 - direct_weight) * prior_ret,
        )
        if q10 is not None and q90 is not None
        else None
    )

    p_up = None
    dproba = getattr(traj, "direction_proba", None)
    if dproba is not None and len(dproba):
        p_up = _finite_or_none(dproba.iloc[-1])

    return ArmPath(points=out, engine="local_tsfm", kind="learned", band=band, p_up=p_up)


def _tsfm_path(
    eng: KostolanyEngine,
    last_close: float,
    dates: list[str],
    proba: dict[str, float],
    *,
    direct_weight: float = 0.85,
) -> ArmPath:
    """Build a deterministic path from direct h5/h20/h63 return forecasts.

    The regime prior remains a conservative shrinkage target, but no longer
    invents the entire 3-month path from a fixed lookup table.
    """
    traj = getattr(eng.model, "last_traj_", None)
    return arm_from_trajectory(traj, last_close, dates, proba, direct_weight=direct_weight)


def assemble_paths(
    analyst_probas: dict[str, dict[str, float]],
    fwd_dates: list[str],
    learned_arm: ArmPath,
    *,
    base: float = 100.0,
    prior_band: dict[str, float] | None = None,
) -> dict[str, Any]:
    """The exact arm/consensus arithmetic that reaches the screen — pure.

    The two prior arms (리듬이/눈치왕) are closed-form functions of the regime
    probability vectors; the learned arm is built by the caller (serving:
    pooled/_tsfm_path, harness: origin-time LocalTSFM). Serving and the
    harness MUST both route through this function — scoring anything else
    recreates the "gate scores what is not shipped" defect.
    """
    arms: dict[str, ArmPath] = {}
    for aid in ("hmm", "gbm", "tsfm"):
        proba = dict(analyst_probas[aid])
        if aid == "hmm":
            arms[aid] = ArmPath(
                points=_path_from_proba(
                    base, proba, fwd_dates, seed=11, stickiness=0.92, shock=0.0
                ),
                engine="regime_prior",
                kind="regime_prior",
                # A prior arm has no predictive distribution of its own; the
                # stated band is the UNCONDITIONAL trailing 63d-return spread,
                # so a (near-)flat centre line ships with honest width.
                band=prior_band,
                p_up=None,
            )
        elif aid == "gbm":
            arms[aid] = ArmPath(
                points=_path_from_proba(
                    base, proba, fwd_dates, seed=29, stickiness=0.72, shock=0.0
                ),
                engine="regime_prior",
                kind="regime_prior",
                band=prior_band,
                p_up=None,
            )
        else:
            arms[aid] = learned_arm
    ends = {
        aid: (float(a.points[-1]["value"]) if a.points else float(base))
        for aid, a in arms.items()
    }
    consensus_level = float(np.mean(list(ends.values()))) if ends else float(base)
    return {
        "arms": arms,
        "ends": ends,
        "consensus_level": consensus_level,
        "consensus_change_pct": (consensus_level / float(base) - 1.0) * 100.0,
        "consensus_outlook": _outlook((consensus_level / float(base) - 1.0) * 100.0),
        "forecast_engine": learned_arm.engine,
    }


def build_sector_flow(
    sector_id: str,
    *,
    use_cache: bool = True,
    refresh: bool = False,
    wait: bool = True,
) -> dict[str, Any] | None:
    """Serve cache immediately; refresh only in background when possible.

    - Fresh cache → return
    - Stale / refresh requested + cache exists → return stale, rebuild in bg
    - Cold miss + wait=True → compute synchronously
    - Cold miss + wait=False → schedule bg rebuild, return None (HTTP 202)
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
        if not wait:
            _schedule_rebuild(sector_id)
            return None
        return _compute_sector_flow(sector_id)

    if use_cache and cached is not None:
        if cached.get("stale"):
            _schedule_rebuild(sector_id)
            cached = read_flow_cache(sector_id) or cached
            cached["refreshing"] = True
        return cached

    if not wait:
        _schedule_rebuild(sector_id)
        return None
    return _compute_sector_flow(sector_id)


def _flow_disclaimer(prior_labels: list[str]) -> str:
    """State plainly which arms are learned and which are prior scenarios.

    Two of the three paths are a closed form over six hardcoded regime drift
    constants, so calling them independent AI forecasts would be false.
    """
    n = len(prior_labels)
    if n >= len(ANALYSTS):
        head = f"3개 경로 모두({'·'.join(prior_labels)}) 학습된 예측이 아니라 국면 사전확률 시나리오입니다."
    elif n:
        head = (
            f"3개 경로 중 {n}개({'·'.join(prior_labels)})는 학습된 예측이 아니라 "
            "국면 사전확률로 만든 시나리오입니다."
        )
    else:
        head = "3개 경로는 서로 다른 추정기의 출력입니다."
    return (
        "실데이터(지수=100). "
        f"{head} 3개월 수치는 예측이 아니라 교육용 참고값이며 투자 권유 아님."
    )


def _compute_sector_flow(sector_id: str) -> dict[str, Any]:
    meta = _sector_meta(sector_id)
    path = _cache_path(sector_id)

    # Fit once: TSFMEnsemble already contains fitted HMM + GBM arms.
    snaps: dict[str, Any] = {}
    engines = fit_analyst_bundle(meta["symbol"])
    for a in ANALYSTS:
        eng = engines[a["id"]]
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
    # The one arm that can be learned: promoted pooled cross-asset head
    # (equity); LocalTSFM fallback. Built here, assembled below through the
    # same pure function the harness scores.
    tsfm_proba = snaps["tsfm"]["probabilities"]
    pooled = _pooled_path(
        meta["symbol"],
        100.0,
        fwd_dates,
        tsfm_proba,
        direct_weight=0.92,
    )
    learned_arm = pooled or _tsfm_path(
        engines["tsfm"],
        100.0,
        fwd_dates,
        tsfm_proba,
        direct_weight=0.90,
    )
    # Stated band for the prior arms: unconditional trailing 63d return spread
    # over the full loaded history (descriptive, never a model output).
    full_closes = ohlcv["close"].dropna().astype(float).sort_index()
    r63 = (full_closes / full_closes.shift(63) - 1.0).dropna()
    prior_band = None
    if len(r63) > 252:
        prior_band = _band_levels(
            100.0, float(r63.quantile(0.10)), float(r63.quantile(0.90))
        )
    assembled = assemble_paths(
        {aid: snaps[aid]["probabilities"] for aid in ("hmm", "gbm", "tsfm")},
        fwd_dates,
        learned_arm,
        prior_band=prior_band,
    )
    forecast_engine = assembled["forecast_engine"]

    forecasts = []
    for a in ANALYSTS:
        arm = assembled["arms"][a["id"]]
        end = assembled["ends"][a["id"]]
        row: dict[str, Any] = {
            "id": a["id"],
            "label": a["label"],
            "color": a["color"],
            "regime": snaps[a["id"]]["regime"],
            "confidence": snaps[a["id"]]["confidence"],
            "outlook": _outlook((end / 100.0 - 1.0) * 100.0),
            "change_pct": round((end / 100.0 - 1.0) * 100.0, 2),
            "points": arm.points,
            "engine": arm.engine,
            "arm_kind": arm.kind,
            "band": arm.band,
            "p_up": round(arm.p_up, 4) if arm.p_up is not None else None,
        }
        forecasts.append(row)

    # Consensus: average of three terminal levels (inside assemble_paths so the
    # harness scores the identical arithmetic).
    consensus = assembled["consensus_level"]

    # Base rate P(up) must sit next to the model's P(up) so the reader can see
    # how far (if at all) the model moves off it. Descriptive, never a feature.
    base_rate_up, base_rate_n = _up_rate(ohlcv["close"])
    prior_labels = [str(f["label"]) for f in forecasts if f["arm_kind"] == "regime_prior"]

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
            "outlook": _outlook((consensus / 100.0 - 1.0) * 100.0),
        },
        "forecast_engine": forecast_engine,
        "base_rate_up": round(base_rate_up, 4) if base_rate_up is not None else None,
        "base_rate_n": base_rate_n,
        "disclaimer": _flow_disclaimer(prior_labels),
        "built_at": _iso(),
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        from kostolany import blob_cache

        blob_cache.push_async(path)
    except Exception:  # noqa: BLE001
        pass
    return payload
