"""Browser Web Push subscriptions + daily regime metric dispatch.

Replaces email newsletter: subscribers opt in on the web client; Cloud Scheduler
hits ``POST /api/push/dispatch`` once per day.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kostolany.blob_cache import pull_blob, push_blob_async

log = logging.getLogger(__name__)


def cron_secret_ok(request_secret: str | None) -> bool:
    expected = (os.environ.get("NEWSLETTER_CRON_SECRET") or "").strip()
    if not expected:
        return False
    return bool(request_secret) and request_secret == expected

_LOCK = threading.Lock()
_GCS_SUBS = "push/subscriptions.jsonl"
_GCS_LAST = "push/last_dispatch.json"
DEFAULT_HOUR_KST = 22  # 22:00 KST


def site_url() -> str:
    return (os.environ.get("NEWSLETTER_SITE_URL") or "https://kostolany-watch.web.app").rstrip("/")


def _store_path() -> Path:
    root = Path(os.environ.get("KOSTOLANY_CACHE_DIR") or "artifacts")
    return root / "push" / "subscriptions.jsonl"


def vapid_public_key() -> str:
    return (os.environ.get("VAPID_PUBLIC_KEY") or "").strip()


def vapid_private_key() -> str:
    """PEM string, optionally with literal \\n or urlsafe-base64 of PEM."""
    import base64

    raw = (os.environ.get("VAPID_PRIVATE_KEY") or "").strip().strip('"').strip("'")
    if not raw:
        return ""
    if "BEGIN" in raw:
        return raw.replace("\\n", "\n")
    pad = "=" * (-len(raw) % 4)
    try:
        decoded = base64.urlsafe_b64decode(raw + pad).decode("utf-8")
        if "BEGIN" in decoded:
            return decoded
    except Exception:  # noqa: BLE001
        pass
    return raw.replace("\\n", "\n")


def vapid_mailto() -> str:
    return (os.environ.get("VAPID_MAILTO") or "mailto:ops@lionandthelab.com").strip()


def vapid_configured() -> bool:
    return bool(vapid_public_key() and vapid_private_key())


def load_subscriptions(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or _store_path()
    # pull_blob(local_path, gcs_blob_path) — args were previously swapped (500 on subscribe).
    pull_blob(p, _GCS_SUBS)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _write_all(rows: list[dict[str, Any]], path: Path | None = None) -> None:
    p = path or _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + ("\n" if rows else "")
    p.write_text(text, encoding="utf-8")
    push_blob_async(p, _GCS_SUBS, content_type="application/x-ndjson")


def upsert_subscription(sub: dict[str, Any]) -> dict[str, str]:
    """Upsert a PushSubscription JSON (+ optional hour_kst, locale)."""
    endpoint = str(sub.get("endpoint") or "").strip()
    keys = sub.get("keys") or {}
    if not endpoint.startswith("https://") or not isinstance(keys, dict):
        raise ValueError("invalid_subscription")
    if not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("invalid_subscription")

    hour = sub.get("hour_kst", DEFAULT_HOUR_KST)
    try:
        hour_i = int(hour)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_hour") from exc
    if hour_i < 0 or hour_i > 23:
        raise ValueError("invalid_hour")

    row = {
        "endpoint": endpoint,
        "keys": {"p256dh": str(keys["p256dh"]), "auth": str(keys["auth"])},
        "hour_kst": hour_i,
        "locale": str(sub.get("locale") or "ko")[:16],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "active": True,
    }
    with _LOCK:
        rows = load_subscriptions()
        out: list[dict[str, Any]] = []
        found = False
        for r in rows:
            if r.get("endpoint") == endpoint:
                out.append({**r, **row})
                found = True
            elif r.get("active", True):
                out.append(r)
        if not found:
            out.append(row)
        # Cap store size
        out = out[-2000:]
        _write_all(out)
    return {"status": "ok" if found else "created"}


def deactivate_subscription(endpoint: str) -> dict[str, str]:
    endpoint = (endpoint or "").strip()
    if not endpoint:
        raise ValueError("invalid_subscription")
    with _LOCK:
        rows = load_subscriptions()
        changed = False
        out = []
        for r in rows:
            if r.get("endpoint") == endpoint:
                if r.get("active", True):
                    changed = True
                out.append({**r, "active": False, "updated_at": datetime.now(timezone.utc).isoformat()})
            else:
                out.append(r)
        if changed:
            _write_all(out)
    return {"status": "ok"}


def build_daily_metric_payload() -> dict[str, Any]:
    """Short regime snapshot for notification body (no AI brief)."""
    from kostolany.watch_cache import read_watch_cache

    bits_ko: list[str] = []
    bits_en: list[str] = []
    for sym, label_ko, label_en in (
        ("^GSPC", "미국", "US"),
        ("BTC-USD", "비트코인", "BTC"),
    ):
        regime = "—"
        name = ""
        try:
            cached = read_watch_cache(sym, "momo,hmm,gbm,tsfm", 360, 2, allow_stale=True)
            if cached and cached.get("analysts"):
                analysts = cached["analysts"]
                pick = next((a for a in analysts if a.get("id") == "momo"), analysts[0])
                snap = pick.get("snapshot") or {}
                regime = str(snap.get("regime") or "—")
                name = str(snap.get("regime_name_ko") or "")
        except Exception as exc:  # noqa: BLE001
            log.warning("push metric %s: %s", sym, exc)
        bits_ko.append(f"{label_ko} {regime}" + (f"({name})" if name else ""))
        bits_en.append(f"{label_en} {regime}")

    title_ko = "Kostolany Watch · 일일 국면"
    title_en = "Kostolany Watch · daily regime"
    body_ko = " · ".join(bits_ko) + " — 교육용, 투자 권유 아님"
    body_en = " · ".join(bits_en) + " — educational, not advice"
    return {
        "title_ko": title_ko,
        "title_en": title_en,
        "body_ko": body_ko[:180],
        "body_en": body_en[:180],
        "url": f"{site_url()}/watch",
    }


def _send_one(sub: dict[str, Any], payload: dict[str, Any]) -> str:
    """Return ok|gone|error."""
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        log.error("pywebpush not installed")
        return "error"

    locale = (sub.get("locale") or "ko").lower()
    title = payload["title_en"] if locale.startswith("en") else payload["title_ko"]
    body = payload["body_en"] if locale.startswith("en") else payload["body_ko"]
    data = {"url": payload["url"], "title": title, "body": body}
    try:
        webpush(
            subscription_info={
                "endpoint": sub["endpoint"],
                "keys": sub["keys"],
            },
            data=json.dumps(data, ensure_ascii=False),
            vapid_private_key=vapid_private_key(),
            vapid_claims={"sub": vapid_mailto()},
            ttl=60 * 60 * 12,
        )
        return "ok"
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in {404, 410}:
            return "gone"
        log.warning("webpush fail: %s", exc)
        return "error"
    except Exception as exc:  # noqa: BLE001
        log.warning("webpush error: %s", exc)
        return "error"


def dispatch_daily(*, hour_kst: int | None = None, force: bool = False) -> dict[str, Any]:
    """Send daily metric push to active subscribers matching hour_kst."""
    if not vapid_configured():
        return {"ok": False, "error": "vapid_not_configured"}

    # Default: current hour in KST
    if hour_kst is None:
        # UTC+9
        now = datetime.now(timezone.utc)
        hour_kst = (now.hour + 9) % 24

    payload = build_daily_metric_payload()
    rows = load_subscriptions()
    active = [r for r in rows if r.get("active", True)]
    targets = [
        r
        for r in active
        if force or int(r.get("hour_kst", DEFAULT_HOUR_KST)) == int(hour_kst)
    ]

    sent = gone = errors = 0
    survivors: list[dict[str, Any]] = []
    endpoint_gone: set[str] = set()
    for sub in targets:
        result = _send_one(sub, payload)
        if result == "ok":
            sent += 1
        elif result == "gone":
            gone += 1
            endpoint_gone.add(str(sub.get("endpoint")))
        else:
            errors += 1

    for r in rows:
        if r.get("endpoint") in endpoint_gone:
            survivors.append({**r, "active": False})
        else:
            survivors.append(r)
    if gone:
        with _LOCK:
            _write_all(survivors)

    marker = {
        "at": datetime.now(timezone.utc).isoformat(),
        "hour_kst": hour_kst,
        "sent": sent,
        "gone": gone,
        "errors": errors,
        "targets": len(targets),
        "payload": payload,
    }
    marker_path = _store_path().parent / "last_dispatch.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    push_blob_async(marker_path, _GCS_LAST, content_type="application/json")
    return {"ok": True, **{k: marker[k] for k in ("hour_kst", "sent", "gone", "errors", "targets")}}
