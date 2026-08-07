"""Browser Web Push subscriptions + daily regime metric dispatch.

Replaces email newsletter: subscribers opt in on the web client; Cloud Scheduler
hits ``POST /api/push/dispatch`` once per day.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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


def _b64url_compare(value: str) -> str:
    """Padding/alphabet-insensitive form, for comparing two base64 spellings."""
    return value.strip().replace("+", "-").replace("/", "_").rstrip("=")


def _strip_pem_armor(pem: str) -> str:
    """PEM -> bare base64 DER body: no BEGIN/END lines, no newlines."""
    return "".join(
        ln.strip()
        for ln in pem.replace("\\n", "\n").splitlines()
        if ln.strip() and not ln.strip().startswith("-----")
    )


def _normalize_vapid_private_key(raw: str) -> str:
    """Any stored spelling of the key -> the bare base64 DER body py_vapid eats.

    Do NOT hand py_vapid a PEM. pywebpush 2.3.0 routes a ``str`` key to
    ``py_vapid.Vapid.from_string()``, and in py_vapid 1.9.4 that method only
    strips newlines and then b64urldecodes the *whole* string — the
    ``-----BEGIN PRIVATE KEY-----`` armor included — before handing it to
    ``from_der``. The armor is not base64, so the bytes are garbage and
    cryptography raises "Could not deserialize key data ... ASN.1 parsing
    error: invalid length". That is exactly what production logged twice a day
    on 2026-08-02..04 while POST /api/push/dispatch still answered 200:
    every single push failed and nothing said so. Only ``from_pem`` /
    ``from_file`` understand the armor and pywebpush never reaches them for a
    str key, so the armor has to come off here.

    Accepted inputs: (a) PEM, (b) PEM with literal ``\\n``, (c) urlsafe-base64
    of a PEM — what .env and Cloud Run actually store, see
    scripts/generate_vapid_keys.py — and (d) an already-bare DER base64 body.
    """
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        return ""
    if "BEGIN" in raw:
        return _strip_pem_armor(raw)
    compact = "".join(raw.split())
    try:
        decoded = base64.urlsafe_b64decode(compact + "=" * (-len(compact) % 4))
        text = decoded.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return compact  # not text: already a DER body
    if "BEGIN" in text:
        return _strip_pem_armor(text)
    return compact


def _vapid_key_format(raw: str) -> str:
    """Which of the four accepted spellings we got — surfaced in diagnostics."""
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        return "empty"
    if "BEGIN" in raw:
        return "pem_escaped" if "\\n" in raw else "pem"
    compact = "".join(raw.split())
    try:
        if "BEGIN" in base64.urlsafe_b64decode(compact + "=" * (-len(compact) % 4)).decode("utf-8"):
            return "base64_pem"
    except (binascii.Error, UnicodeDecodeError, ValueError):
        pass
    return "der_base64"


def vapid_private_key() -> str:
    """Bare base64 DER body — see _normalize_vapid_private_key for why not PEM."""
    return _normalize_vapid_private_key(os.environ.get("VAPID_PRIVATE_KEY") or "")


def vapid_mailto() -> str:
    return (os.environ.get("VAPID_MAILTO") or "mailto:ops@lionandthelab.com").strip()


# Keyed on the raw env pair so a key swap invalidates it; holds the parsed
# Vapid instance so we parse DER once per process, not once per subscriber.
_VAPID_CACHE: dict[str, Any] = {}


def _build_vapid_state(raw: str, public: str) -> tuple[dict[str, Any], Any]:
    state: dict[str, Any] = {
        "ok": False,
        "reason": "missing_private_key",
        "format": _vapid_key_format(raw),
        "public_key_present": bool(public),
        "public_key_matches": None,
    }
    normalized = _normalize_vapid_private_key(raw)
    if not normalized:
        return state, None
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from py_vapid import Vapid
    except ImportError as exc:
        state["reason"] = f"import_error: {exc}"[:200]
        return state, None
    try:
        vapid = Vapid.from_string(private_key=normalized)
        # Parseable is not the same as usable: do one real ECDSA sign so
        # "configured" cannot mean "the env var was set to something".
        vapid.private_key.sign(b"kostolany-vapid-selftest", ec.ECDSA(hashes.SHA256()))
        derived = (
            base64.urlsafe_b64encode(
                vapid.public_key.public_bytes(
                    encoding=serialization.Encoding.X962,
                    format=serialization.PublicFormat.UncompressedPoint,
                )
            )
            .decode("ascii")
            .rstrip("=")
        )
    except Exception as exc:  # noqa: BLE001 — py_vapid raises bare Exception subclasses
        state["reason"] = f"{type(exc).__name__}: {exc}"[:200]
        return state, None
    state["ok"] = True
    state["reason"] = None
    state["public_key_matches"] = bool(public) and _b64url_compare(public) == derived
    return state, vapid


def vapid_selftest(*, refresh: bool = False) -> dict[str, Any]:
    """Can we actually sign with VAPID_PRIVATE_KEY, and does it match the public key?

    Lazy and cached: never runs at import time, no network, one DER parse plus
    one ECDSA sign per distinct env value. A ``public_key_matches`` of False
    means subscriptions minted with VAPID_PUBLIC_KEY will be rejected 403 by
    the push service even though signing works — reported, not fatal, because
    a mismatch here is an ops problem and should not also take /push/subscribe
    down on a base64 spelling quirk.
    """
    raw = (os.environ.get("VAPID_PRIVATE_KEY") or "").strip()
    public = vapid_public_key()
    cache_key = f"{raw}\x00{public}"
    if not refresh and _VAPID_CACHE.get("key") == cache_key:
        return dict(_VAPID_CACHE["state"])
    state, vapid = _build_vapid_state(raw, public)
    _VAPID_CACHE["key"] = cache_key
    _VAPID_CACHE["state"] = state
    _VAPID_CACHE["vapid"] = vapid
    if not state["ok"]:
        log.warning("vapid key unusable (%s): %s", state["format"], state["reason"])
    elif state["public_key_matches"] is False:
        log.error("vapid private key does not match VAPID_PUBLIC_KEY — pushes will 403")
    return dict(state)


def _vapid_signer() -> Any:
    """Parsed Vapid instance, or None. Passing the object (not a string) also
    keeps pywebpush out of its os.path.isfile()/from_string() guessing branch."""
    vapid_selftest()
    return _VAPID_CACHE.get("vapid")


def vapid_configured() -> bool:
    """True only when the key loads AND signs — not merely "the env var exists".

    The old env-presence check is how a PEM-shaped key shipped to Cloud Run and
    failed 100% of sends for three days (2026-08-02..04) while every health
    surface reported configured.
    """
    return bool(vapid_public_key()) and bool(vapid_selftest()["ok"])


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


def _endpoint_host(endpoint: Any) -> str:
    """Host only — the endpoint path is the subscriber's bearer secret."""
    try:
        return urlsplit(str(endpoint or "")).netloc or "?"
    except ValueError:
        return "?"


def _send_one(sub: dict[str, Any], payload: dict[str, Any]) -> tuple[str, str, str]:
    """Return (status, reason, message); status is ok|gone|error.

    reason/message were added 2026-08-07: the old ``str`` return collapsed every
    cause into "error", so a run where all sends failed was indistinguishable
    from a clean one in the dispatch response.
    """
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        log.error("pywebpush not installed")
        return "error", "pywebpush_missing", ""

    signer = _vapid_signer()
    if signer is None:
        return "error", "vapid_key_unusable", str(vapid_selftest().get("reason") or "")

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
            vapid_private_key=signer,
            vapid_claims={"sub": vapid_mailto()},
            ttl=60 * 60 * 12,
        )
        return "ok", "", ""
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in {404, 410}:
            return "gone", f"http_{status}", ""
        reason = f"http_{status}" if status else "webpush_exception"
        log.warning("webpush fail (%s): %s", reason, exc)
        return "error", reason, str(exc)[:300]
    except Exception as exc:  # noqa: BLE001
        log.warning("webpush error: %s", exc)
        return "error", f"exception_{type(exc).__name__}", str(exc)[:300]


def _normalize_send_result(result: Any) -> tuple[str, str, str]:
    """Tolerate the pre-2026-08-07 plain-str return (tests still stub it that way)."""
    if isinstance(result, str):
        return result, ("" if result == "ok" else result), ""
    parts = [*list(result), "", ""]
    return str(parts[0]), str(parts[1]), str(parts[2])


def dispatch_daily(*, hour_kst: int | None = None, force: bool = False) -> dict[str, Any]:
    """Send daily metric push to active subscribers matching hour_kst.

    The response is the only place a Cloud Scheduler run is visible — it always
    returns 200 — so it carries the per-reason failure breakdown, not just counts.
    """
    vapid = vapid_selftest()
    if not vapid_configured():
        log.error("push dispatch skipped: vapid unusable (%s)", vapid.get("reason"))
        return {
            "ok": False,
            "error": "vapid_not_configured",
            "targets": 0,
            "sent": 0,
            "gone": 0,
            "errors": 0,
            "failures": {"vapid_not_configured": 1},
            "failure_samples": [],
            "vapid": vapid,
        }

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
    failures: dict[str, int] = {}
    failure_samples: list[dict[str, str]] = []
    survivors: list[dict[str, Any]] = []
    endpoint_gone: set[str] = set()
    for sub in targets:
        status, reason, message = _normalize_send_result(_send_one(sub, payload))
        if status == "ok":
            sent += 1
        elif status == "gone":
            gone += 1
            endpoint_gone.add(str(sub.get("endpoint")))
        else:
            errors += 1
            key = reason or "unknown"
            failures[key] = failures.get(key, 0) + 1
            if len(failure_samples) < 5:
                failure_samples.append(
                    {"reason": key, "host": _endpoint_host(sub.get("endpoint")), "message": message}
                )

    if errors:
        log.error("push dispatch: %d/%d sends failed — %s", errors, len(targets), failures)

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
        "failures": failures,
        "failure_samples": failure_samples,
        "vapid": vapid,
        "payload": payload,
    }
    marker_path = _store_path().parent / "last_dispatch.json"
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
    push_blob_async(marker_path, _GCS_LAST, content_type="application/json")
    return {
        "ok": True,
        **{
            k: marker[k]
            for k in (
                "hour_kst",
                "sent",
                "gone",
                "errors",
                "targets",
                "failures",
                "failure_samples",
                "vapid",
            )
        },
    }
