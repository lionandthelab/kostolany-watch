"""Disk cache for /watch payloads — 6h soft TTL, 1h refresh cooldown."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from kostolany.settings import get_settings
from kostolany import blob_cache

WATCH_TTL_HOURS = 6.0
REFRESH_COOLDOWN_HOURS = 1.0


def _path(symbol: str, models: str, limit: int, stride: int) -> Path:
    safe_sym = "".join(c if c.isalnum() or c in "-_." else "_" for c in symbol.upper())
    safe_models = "".join(c if c.isalnum() or c in ",-_." else "_" for c in models)
    name = f"watch_{safe_sym}_{safe_models}_L{limit}_S{stride}.json"
    return get_settings().cache_path / name


def _symbol_refresh_path(symbol: str) -> Path:
    safe_sym = "".join(
        c if c.isalnum() or c in "-_." else "_" for c in symbol.upper()
    )
    return get_settings().cache_path / f"watch_{safe_sym}_refresh.json"


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def read_watch_cache(
    symbol: str,
    models: str,
    limit: int,
    stride: int,
    *,
    max_age_hours: float = WATCH_TTL_HOURS,
    allow_stale: bool = False,
) -> dict[str, Any] | None:
    path = _path(symbol, models, limit, stride)
    blob_cache.pull_if_missing(path)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    cached_at = float(payload.get("cached_at_epoch", 0))
    age_h = (time.time() - cached_at) / 3600.0
    stale = age_h > max_age_hours
    if stale and not allow_stale:
        return None
    last_refresh = float(payload.get("last_refresh_epoch", cached_at))
    refresh_ready = last_refresh + REFRESH_COOLDOWN_HOURS * 3600.0
    body = payload.get("body")
    if not isinstance(body, dict) or not body.get("analysts"):
        return None
    out = dict(body)
    out["cached"] = True
    out["stale"] = stale
    out["cache_age_hours"] = round(age_h, 3)
    out["cached_at"] = _iso(cached_at)
    out["expires_at"] = _iso(cached_at + max_age_hours * 3600.0)
    out["can_refresh"] = time.time() >= refresh_ready
    out["refresh_available_at"] = _iso(refresh_ready)
    return out


def write_watch_cache(
    symbol: str,
    models: str,
    limit: int,
    stride: int,
    body: dict[str, Any],
    *,
    refreshed: bool = False,
) -> dict[str, Any]:
    path = _path(symbol, models, limit, stride)
    now = time.time()
    prev_refresh = 0.0
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            prev_refresh = float(prev.get("last_refresh_epoch", 0))
        except Exception:  # noqa: BLE001
            prev_refresh = 0.0
    last_refresh = now if refreshed else prev_refresh
    refresh_ready = last_refresh + REFRESH_COOLDOWN_HOURS * 3600.0
    envelope = {
        "cached_at_epoch": now,
        "last_refresh_epoch": last_refresh,
        "body": body,
    }
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    blob_cache.push_async(path)
    out = dict(body)
    out["cached"] = False
    out["cached_at"] = _iso(now)
    out["expires_at"] = _iso(now + WATCH_TTL_HOURS * 3600.0)
    out["can_refresh"] = time.time() >= refresh_ready
    out["refresh_available_at"] = _iso(refresh_ready) if last_refresh > 0 else _iso(now)
    return out


def mark_refresh_started(
    symbol: str,
    models: str,
    limit: int,
    stride: int,
) -> dict[str, Any]:
    """Record a refresh attempt (starts 1h cooldown) without requiring a full body."""
    path = _path(symbol, models, limit, stride)
    now = time.time()
    body: dict[str, Any] | None = None
    cached_at = now
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            body = prev.get("body") if isinstance(prev.get("body"), dict) else None
            cached_at = float(prev.get("cached_at_epoch", now))
        except Exception:  # noqa: BLE001
            pass
    envelope = {
        "cached_at_epoch": cached_at,
        "last_refresh_epoch": now,
        "body": body,
    }
    path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    from kostolany import blob_cache

    blob_cache.push_async(path)
    refresh_ready = now + REFRESH_COOLDOWN_HOURS * 3600.0
    return {
        "can_refresh": False,
        "refresh_available_at": _iso(refresh_ready),
        "cached_at": _iso(cached_at) if body else None,
    }


def refresh_cooldown_remaining(
    symbol: str,
    models: str,
    limit: int,
    stride: int,
) -> float:
    """Seconds until refresh is allowed; 0 if allowed or no prior refresh."""
    path = _path(symbol, models, limit, stride)
    if not path.exists():
        return 0.0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0.0
    last_refresh = float(payload.get("last_refresh_epoch", 0))
    if last_refresh <= 0:
        return 0.0
    ready = last_refresh + REFRESH_COOLDOWN_HOURS * 3600.0
    return max(0.0, ready - time.time())


def mark_symbol_refresh_started(symbol: str) -> None:
    """Persist a symbol-wide cooldown so cache-shape variation cannot bypass it."""
    path = _symbol_refresh_path(symbol)
    path.write_text(
        json.dumps({"last_refresh_epoch": time.time()}),
        encoding="utf-8",
    )
    blob_cache.push_async(path)


def symbol_refresh_cooldown_remaining(symbol: str) -> float:
    path = _symbol_refresh_path(symbol)
    blob_cache.pull_if_missing(path)
    if not path.exists():
        return 0.0
    try:
        last_refresh = float(
            json.loads(path.read_text(encoding="utf-8")).get(
                "last_refresh_epoch", 0.0
            )
        )
    except Exception:  # noqa: BLE001
        return 0.0
    ready = last_refresh + REFRESH_COOLDOWN_HOURS * 3600.0
    return max(0.0, ready - time.time())
