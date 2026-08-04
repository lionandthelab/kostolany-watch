"""Crypto Fear & Greed Index (Alternative.me) for the Macro desk.

Educational sentiment gauge — not investment advice.
API: https://api.alternative.me/fng/
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from kostolany.settings import get_settings

log = logging.getLogger(__name__)

GAUGE_TTL_HOURS = 6.0
_CACHE_NAME = "macro_crypto_fear_greed_v1.json"
_API = "https://api.alternative.me/fng/"
_SERIES_LIMIT = 90

_CLASS_KO = {
    "extreme fear": "극단적 공포",
    "fear": "공포",
    "neutral": "중립",
    "greed": "탐욕",
    "extreme greed": "극단적 탐욕",
}


def _path():
    return get_settings().cache_path / _CACHE_NAME


def _label_en(classification: str | None, score: float) -> str:
    if classification:
        return classification.strip().title()
    if score < 20:
        return "Extreme Fear"
    if score < 40:
        return "Fear"
    if score < 60:
        return "Neutral"
    if score < 80:
        return "Greed"
    return "Extreme Greed"


def _label_ko(classification: str | None, score: float) -> str:
    if classification:
        mapped = _CLASS_KO.get(classification.strip().lower())
        if mapped:
            return mapped
    from kostolany.fear_greed import _label_ko as equity_label_ko

    return equity_label_ko(score)


def compute_crypto_fear_greed() -> dict[str, Any]:
    timeout = float(getattr(get_settings(), "http_timeout", 20.0) or 20.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(_API, params={"limit": _SERIES_LIMIT, "format": "json"})
        r.raise_for_status()
        payload = r.json()

    rows = payload.get("data") or []
    if not rows:
        raise ValueError("empty crypto fear-greed response")

    series: list[dict[str, Any]] = []
    # API returns newest-first; reverse for chronological sparklines.
    for row in reversed(rows):
        try:
            score = float(row["value"])
            ts = int(row["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        series.append({"date": day, "value": round(score, 1)})

    if not series:
        raise ValueError("no usable crypto fear-greed points")

    latest = rows[0]
    score = float(latest["value"])
    classification = str(latest.get("value_classification") or "").strip()
    asof = series[-1]["date"]

    return {
        "score": score,
        "label": _label_ko(classification, score),
        "label_ko": _label_ko(classification, score),
        "label_en": _label_en(classification, score),
        "classification": classification,
        "scale": "crypto_fear_greed_0_100",
        "asof": asof,
        "source": "alternative.me",
        "series": series,
        "disclaimer": "Educational crypto sentiment (Alternative.me) — not investment advice.",
        "disclaimer_ko": "교육용 가상화폐 심리 지표(Alternative.me)입니다. 투자 권유가 아닙니다.",
        "disclaimer_en": "Educational crypto sentiment (Alternative.me) — not investment advice.",
    }


def get_crypto_fear_greed(*, force: bool = False) -> dict[str, Any]:
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
        payload = compute_crypto_fear_greed()
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
        log.warning("crypto fear-greed fetch failed: %s", exc)
        if path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                cached["stale"] = True
                cached["error"] = str(exc)[:160]
                return cached
            except Exception:  # noqa: BLE001
                pass
        return {
            "score": None,
            "label": "—",
            "label_ko": "—",
            "label_en": "—",
            "scale": "crypto_fear_greed_0_100",
            "asof": time.strftime("%Y-%m-%d"),
            "source": "fallback",
            "series": [],
            "disclaimer": "Educational crypto sentiment — not investment advice.",
            "error": str(exc)[:160],
        }
