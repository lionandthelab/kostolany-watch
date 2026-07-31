"""Weekly brief email waitlist + Resend delivery.

Collect subscribers in JSONL (local + GCS). Cloud Scheduler hits
``POST /api/newsletter/dispatch`` on Cloud Run — no local device required.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from kostolany.blob_cache import pull_blob, push_blob_async

log = logging.getLogger(__name__)

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)
_LOCK = threading.Lock()
_RATE: dict[str, list[float]] = {}
_RATE_WINDOW_S = 3600.0
_RATE_MAX = 8
_GCS_SUBS = "newsletter/subscribers.jsonl"

DISCLAIMER_KO = (
    "본 정보는 교육·연구 목적의 국면 인식 보조 자료이며 투자 권유·자문이 아닙니다."
)
DISCLAIMER_EN = (
    "For education and research on regime recognition only — not investment advice."
)

RESEND_API = "https://api.resend.com/emails"


def site_url() -> str:
    return (os.environ.get("NEWSLETTER_SITE_URL") or "https://kostolany-watch.web.app").rstrip("/")


def _store_path() -> Path:
    root = Path(os.environ.get("KOSTOLANY_CACHE_DIR") or "artifacts")
    return root / "newsletter" / "subscribers.jsonl"


def normalize_email(raw: str) -> str | None:
    email = (raw or "").strip().lower()
    if not email or len(email) > 254 or not _EMAIL_RE.match(email):
        return None
    return email


def _rate_ok(key: str) -> bool:
    now = time.time()
    with _LOCK:
        hits = [t for t in _RATE.get(key, []) if now - t < _RATE_WINDOW_S]
        if len(hits) >= _RATE_MAX:
            _RATE[key] = hits
            return False
        hits.append(now)
        _RATE[key] = hits
        return True


def _emails_on_disk(path: Path) -> set[str]:
    return {r["email"] for r in load_subscribers(path) if r.get("email")}


def load_subscribers(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or _store_path()
    if not p.exists():
        pull_blob(p, _GCS_SUBS)
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            em = row.get("email")
            if not isinstance(em, str):
                continue
            em = em.lower()
            if em in seen:
                continue
            seen.add(em)
            out.append({**row, "email": em})
    except OSError:
        return []
    return out


def resend_configured() -> bool:
    return bool((os.environ.get("RESEND_API_KEY") or "").strip())


def _from_address() -> str:
    return (
        os.environ.get("RESEND_FROM") or "Kostolany Watch <onboarding@resend.dev>"
    ).strip()


def send_email(*, to: str, subject: str, html: str, text: str) -> dict[str, Any]:
    """Send one email via Resend. Raises RuntimeError if not configured / API error."""
    key = (os.environ.get("RESEND_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("RESEND_API_KEY not set")
    payload = {
        "from": _from_address(),
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }
    with httpx.Client(timeout=30.0) as client:
        res = client.post(
            RESEND_API,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
        )
    if res.status_code >= 400:
        raise RuntimeError(f"resend {res.status_code}: {res.text[:300]}")
    data = res.json() if res.content else {}
    return {"ok": True, "id": data.get("id"), "to": to}


def _welcome_copy(locale: str) -> tuple[str, str, str]:
    if locale == "en":
        subject = "You're on the Kostolany Watch weekly brief list"
        html = (
            "<p>Thanks for subscribing. We'll email when a new weekly regime brief is published.</p>"
            f'<p><a href="{site_url()}/guide/">Read the guide</a> · '
            f'<a href="{site_url()}/watch">Open regime view</a></p>'
            f"<p><em>{DISCLAIMER_EN}</em></p>"
        )
        text = (
            "Thanks for subscribing. We'll email when a new weekly regime brief is published.\n"
            f"Guide: {site_url()}/guide/\n{DISCLAIMER_EN}"
        )
        return subject, html, text
    subject = "Kostolany Watch 주간 브리핑 신청이 완료되었습니다"
    html = (
        "<p>신청해 주셔서 감사합니다. 새 주간 국면 브리핑이 올라가면 메일로 알려 드립니다.</p>"
        f'<p><a href="{site_url()}/guide/">가이드 보기</a> · '
        f'<a href="{site_url()}/watch">국면 열기</a></p>'
        f"<p><em>{DISCLAIMER_KO}</em></p>"
    )
    text = (
        "신청해 주셔서 감사합니다. 새 주간 국면 브리핑이 올라가면 메일로 알려 드립니다.\n"
        f"가이드: {site_url()}/guide/\n{DISCLAIMER_KO}"
    )
    return subject, html, text


def send_welcome(email: str, locale: str = "ko") -> None:
    if not resend_configured():
        log.info("welcome skipped (no RESEND_API_KEY): %s", email)
        return
    loc = "en" if str(locale).lower().startswith("en") else "ko"
    subject, html, text = _welcome_copy(loc)
    try:
        send_email(to=email, subject=subject, html=html, text=text)
    except Exception as exc:  # noqa: BLE001
        log.warning("welcome email failed for %s: %s", email, exc)


def subscribe(
    email_raw: str,
    *,
    locale: str = "ko",
    source: str = "web",
    client_key: str = "anon",
    honeypot: str = "",
    send_welcome_email: bool = True,
) -> dict[str, Any]:
    """Append a subscriber. Idempotent on email. Optionally sends welcome via Resend."""
    if (honeypot or "").strip():
        return {"ok": True, "status": "ok", "disclaimer": DISCLAIMER_KO}

    if not _rate_ok(client_key or "anon"):
        return {"ok": False, "status": "rate_limited", "error": "too_many_requests"}

    email = normalize_email(email_raw)
    if email is None:
        return {"ok": False, "status": "invalid", "error": "invalid_email"}

    loc = "en" if str(locale).lower().startswith("en") else "ko"
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    with _LOCK:
        if not path.exists():
            pull_blob(path, _GCS_SUBS)
        known = _emails_on_disk(path)
        if email in known:
            return {"ok": True, "status": "already", "disclaimer": DISCLAIMER_KO}
        row = {
            "email": email,
            "locale": loc,
            "source": (source or "web")[:64],
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        push_blob_async(path, _GCS_SUBS, content_type="application/x-ndjson")

    if send_welcome_email:
        threading.Thread(
            target=send_welcome,
            args=(email, loc),
            name="newsletter-welcome",
            daemon=True,
        ).start()

    return {
        "ok": True,
        "status": "subscribed",
        "disclaimer": DISCLAIMER_KO,
        "delivery": "resend" if resend_configured() else "waitlist_only",
    }


def fetch_latest_brief_for_email(kind: str = "weekly") -> dict[str, str] | None:
    """Prefer GCS-stored briefs; fall back to static RSS weekly items."""
    try:
        from kostolany.briefs import latest_brief, site_url as brief_site

        brief = latest_brief(kind)  # type: ignore[arg-type]
        if brief:
            lang_title = (brief.get("title") or {}).get("ko") or ""
            lang_desc = (brief.get("description") or {}).get("ko") or ""
            slug = brief["slug"]
            return {
                "slug": slug,
                "link": f"{brief_site()}/guide/{slug}/",
                "title": lang_title,
                "description": lang_desc,
                "kind": kind,
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("briefs lookup failed: %s", exc)

    if kind != "weekly":
        return None
    return fetch_latest_weekly_from_feed()


def fetch_latest_weekly_from_feed(feed_url: str | None = None) -> dict[str, str] | None:
    """Parse production RSS; return latest item whose link contains /guide/weekly-."""
    url = feed_url or f"{site_url()}/guide/feed.xml"
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        res = client.get(url)
        res.raise_for_status()
        raw = res.text
    root = ET.fromstring(raw)
    for item in root.findall("./channel/item"):
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        if "/guide/weekly-" not in link:
            continue
        slug = urlparse(link).path.rstrip("/").split("/")[-1]
        return {"slug": slug, "link": link, "title": title, "description": desc, "kind": "weekly"}
    return None


def _brief_email(locale: str, brief: dict[str, str]) -> tuple[str, str, str]:
    title = brief["title"]
    link = brief["link"]
    desc = brief.get("description") or ""
    kind = brief.get("kind") or "weekly"
    if locale == "en":
        label = "Daily regime card" if kind == "daily" else "Weekly regime brief"
        subject = f"{label} — {brief['slug']}"
        html = (
            f"<p>A new {label.lower()} is live.</p>"
            f"<p><strong>{title}</strong></p>"
            f"<p>{desc}</p>"
            f'<p><a href="{link}">Read</a> · '
            f'<a href="{site_url()}/watch">Open regime view</a></p>'
            f"<p><em>{DISCLAIMER_EN}</em></p>"
            f"<p style=\"color:#666;font-size:12px\">Unsubscribe: reply and ask to be removed "
            f"(manual for now).</p>"
        )
        text = f"{title}\n{desc}\n{link}\n\n{DISCLAIMER_EN}"
        return subject, html, text
    label = "데일리 국면 카드" if kind == "daily" else "주간 국면 브리핑"
    subject = f"{label} — {brief['slug']}"
    html = (
        f"<p>새 {label}이 올라왔습니다.</p>"
        f"<p><strong>{title}</strong></p>"
        f"<p>{desc}</p>"
        f'<p><a href="{link}">읽기</a> · '
        f'<a href="{site_url()}/watch">국면 열기</a></p>'
        f"<p><em>{DISCLAIMER_KO}</em></p>"
        f"<p style=\"color:#666;font-size:12px\">수신 거부: 회신으로 제외 요청(현재 수동).</p>"
    )
    text = f"{title}\n{desc}\n{link}\n\n{DISCLAIMER_KO}"
    return subject, html, text


def _sent_path_for(kind: str) -> Path:
    root = Path(os.environ.get("KOSTOLANY_CACHE_DIR") or "artifacts")
    return root / "newsletter" / f"last_dispatch_{kind}.json"


def _gcs_sent_for(kind: str) -> str:
    return f"newsletter/last_dispatch_{kind}.json"


def _read_last_dispatch(kind: str = "weekly") -> dict[str, Any]:
    path = _sent_path_for(kind)
    if not path.exists():
        pull_blob(path, _gcs_sent_for(kind))
    # migrate old single file
    if not path.exists() and kind == "weekly":
        legacy = Path(os.environ.get("KOSTOLANY_CACHE_DIR") or "artifacts") / "newsletter" / "last_dispatch.json"
        if not legacy.exists():
            pull_blob(legacy, "newsletter/last_dispatch.json")
        if legacy.exists():
            try:
                return json.loads(legacy.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_last_dispatch(payload: dict[str, Any], kind: str = "weekly") -> None:
    path = _sent_path_for(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    push_blob_async(path, _gcs_sent_for(kind), content_type="application/json")


def dispatch_latest(
    *,
    force: bool = False,
    dry_run: bool = False,
    feed_url: str | None = None,
    kind: str = "weekly",
) -> dict[str, Any]:
    """Send the latest brief of ``kind`` to all subscribers."""
    if kind not in {"weekly", "daily"}:
        return {"ok": False, "status": "invalid_kind", "error": "kind must be weekly|daily"}

    brief = fetch_latest_brief_for_email(kind)
    if brief is None and kind == "weekly" and feed_url:
        brief = fetch_latest_weekly_from_feed(feed_url)
    if brief is None:
        return {"ok": False, "status": "no_brief", "error": f"no {kind} brief available"}

    last = _read_last_dispatch(kind)
    if not force and last.get("slug") == brief["slug"]:
        return {
            "ok": True,
            "status": "already_sent",
            "slug": brief["slug"],
            "kind": kind,
            "sent": last.get("sent", 0),
        }

    subs = load_subscribers()
    if dry_run:
        return {
            "ok": True,
            "status": "dry_run",
            "slug": brief["slug"],
            "kind": kind,
            "subscribers": len(subs),
            "delivery_ready": resend_configured(),
        }

    if not subs:
        return {
            "ok": True,
            "status": "no_subscribers",
            "slug": brief["slug"],
            "kind": kind,
            "sent": 0,
        }

    if not resend_configured():
        return {"ok": False, "status": "not_configured", "error": "RESEND_API_KEY not set"}

    sent = 0
    errors: list[str] = []
    for row in subs:
        loc = "en" if str(row.get("locale", "ko")).startswith("en") else "ko"
        subject, html, text = _brief_email(loc, brief)
        try:
            send_email(to=row["email"], subject=subject, html=html, text=text)
            sent += 1
            time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{row['email']}: {exc}")
            if len(errors) >= 20:
                break

    meta = {
        "slug": brief["slug"],
        "link": brief["link"],
        "kind": kind,
        "sent": sent,
        "errors": len(errors),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if sent > 0:
        _write_last_dispatch(meta, kind)

    return {
        "ok": len(errors) == 0,
        "status": "sent" if sent else "failed",
        "slug": brief["slug"],
        "kind": kind,
        "sent": sent,
        "subscribers": len(subs),
        "error_samples": errors[:5],
        "disclaimer": DISCLAIMER_KO,
    }


def cron_secret_ok(provided: str | None) -> bool:
    expected = (os.environ.get("NEWSLETTER_CRON_SECRET") or "").strip()
    if not expected:
        return False
    return bool(provided) and provided.strip() == expected
