"""Published briefs (weekly essays + daily cards) stored in GCS/local JSONL index."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from kostolany.blob_cache import pull_blob, push_blob_async
from kostolany.regimes import DISCLAIMER_KO

log = logging.getLogger(__name__)

Kind = Literal["weekly", "daily"]

_LOCK = threading.Lock()
_GCS_INDEX = "newsletter/briefs_index.json"
_GCS_PREFIX = "newsletter/briefs"

DISCLAIMER_EN = (
    "For education and research on regime recognition only — not investment advice."
)


def site_url() -> str:
    return (os.environ.get("NEWSLETTER_SITE_URL") or "https://kostolany-watch.web.app").rstrip("/")


def _root() -> Path:
    return Path(os.environ.get("KOSTOLANY_CACHE_DIR") or "artifacts") / "newsletter" / "briefs"


def _index_path() -> Path:
    return _root() / "index.json"


def _brief_path(slug: str) -> Path:
    return _root() / f"{slug}.json"


def _slugify_date(d: date | None = None, *, kind: Kind) -> str:
    day = d or date.today()
    return f"{kind}-{day.isoformat()}"


def _load_index() -> list[dict[str, Any]]:
    path = _index_path()
    if not path.exists():
        pull_blob(path, _GCS_INDEX)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data) if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_index(rows: list[dict[str, Any]]) -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    push_blob_async(path, _GCS_INDEX, content_type="application/json")


def get_brief(slug: str) -> dict[str, Any] | None:
    path = _brief_path(slug)
    gcs = f"{_GCS_PREFIX}/{slug}.json"
    if not path.exists():
        pull_blob(path, gcs)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_briefs(*, kind: Kind | None = None, limit: int = 40) -> list[dict[str, Any]]:
    rows = _load_index()
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    rows = sorted(rows, key=lambda r: str(r.get("date") or ""), reverse=True)
    return rows[: max(1, min(limit, 200))]


def save_brief(payload: dict[str, Any]) -> dict[str, Any]:
    """Upsert a published brief. Required: slug, kind, date, title{ko,en}, body{ko,en}."""
    slug = str(payload.get("slug") or "").strip()
    kind = str(payload.get("kind") or "").strip()
    if kind not in {"weekly", "daily"}:
        raise ValueError("kind must be weekly|daily")
    if not re.fullmatch(r"[a-z0-9][a-z0-9\-_]{2,80}", slug):
        raise ValueError("invalid slug")

    title = payload.get("title") or {}
    body = payload.get("body") or {}
    if not isinstance(title, dict) or not title.get("ko"):
        raise ValueError("title.ko required")
    if not isinstance(body, dict) or not body.get("ko"):
        raise ValueError("body.ko required")

    # Ensure disclaimer present in KO body
    from kostolany.brief_style import sanitize_brief_html, sanitize_plain_copy

    ko_body = sanitize_brief_html(str(body["ko"]))
    en_body = sanitize_brief_html(str(body.get("en") or body["ko"]))
    if "투자 권유" not in ko_body and "disclaimer" not in ko_body:
        ko_body += f'<p class="disclaimer">{DISCLAIMER_KO}</p>'
    if "investment advice" not in en_body.lower() and "disclaimer" not in en_body:
        en_body += f'<p class="disclaimer">{DISCLAIMER_EN}</p>'
    body = {**body, "ko": ko_body, "en": en_body}

    title_ko = sanitize_plain_copy(str(title["ko"]))
    title_en = sanitize_plain_copy(str(title.get("en") or title["ko"]))
    desc_in = payload.get("description") or {}
    desc_ko = sanitize_plain_copy(str(desc_in.get("ko") or title_ko))[:280]
    desc_en = sanitize_plain_copy(str(desc_in.get("en") or title_en or title_ko))[:280]

    brief = {
        "slug": slug,
        "kind": kind,
        "date": str(payload.get("date") or date.today().isoformat()),
        "status": "published",
        "title": {
            "ko": title_ko,
            "en": title_en,
        },
        "description": {
            "ko": desc_ko,
            "en": desc_en,
        },
        "body": {"ko": body["ko"], "en": en_body},
        "source": str(payload.get("source") or "api")[:64],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    with _LOCK:
        path = _brief_path(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        push_blob_async(path, f"{_GCS_PREFIX}/{slug}.json", content_type="application/json")

        idx = [r for r in _load_index() if r.get("slug") != slug]
        idx.append(
            {
                "slug": slug,
                "kind": kind,
                "date": brief["date"],
                "title": brief["title"],
                "description": brief["description"],
                "updated_at": brief["updated_at"],
            }
        )
        idx.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
        _save_index(idx[:200])

    return brief


def latest_brief(kind: Kind) -> dict[str, Any] | None:
    rows = list_briefs(kind=kind, limit=1)
    if not rows:
        return None
    return get_brief(str(rows[0]["slug"]))


def build_daily_card_from_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Deterministic short news-style card from watch/news context (no LLM)."""
    from kostolany.brief_style import (
        compact_context_for_llm,
        first_desk_line,
        sanitize_brief_html,
    )

    today = date.today().isoformat()
    slug = _slugify_date(kind="daily")
    raw_priority = str(ctx.get("priority_summary") or "")
    slim = compact_context_for_llm(
        {
            "markets": ctx.get("markets") or [],
            "news": {
                "items": ctx.get("headlines") or [],
                "priority_summary_md": raw_priority,
            },
        }
    )

    rows = []
    rows_en = []
    for m in slim.get("markets") or []:
        label = m.get("label") or "—"
        regime = m.get("regime") or "—"
        name = m.get("regime_name") or ""
        agree = m.get("agreement") or ""
        bit = f"{regime}" + (f" · {name}" if name else "")
        if agree:
            bit += f" · {agree}"
        rows.append(f"<li><strong>{_esc(str(label))}</strong> — {_esc(bit)}</li>")
        rows_en.append(f"<li><strong>{_esc(str(label))}</strong> — {_esc(bit)}</li>")

    gauge = next((m.get("gauges_note") for m in (slim.get("markets") or []) if m.get("gauges_note")), None)
    if gauge:
        rows.append(f"<li><strong>게이지</strong> — {_esc(gauge)}</li>")
        rows_en.append(f"<li><strong>Gauges</strong> — {_esc(str(gauge))}</li>")

    headlines = slim.get("headlines") or []
    hl = "".join(
        f"<li>{_esc(h.get('title') or '')}"
        + (f" <span class='meta'>{_esc(h.get('theme') or '')}</span>" if h.get("theme") else "")
        + "</li>"
        for h in headlines[:4]
    )
    lead = first_desk_line(raw_priority) or "미국·가상화폐 국면과 오늘 헤드라인을 짧게 정리합니다."
    hook = lead
    for h in headlines:
        title = str(h.get("title") or "").strip()
        if title and any("\uac00" <= ch <= "\ud7a3" for ch in title):
            hook = title
            break
    if len(hook) > 42:
        hook = hook[:40].rstrip() + "…"

    body_ko = sanitize_brief_html(
        f'<p class="lead">{_esc(lead)}</p>'
        f"<h2>한눈에</h2><ul>{''.join(rows) or '<li>데이터 준비 중</li>'}</ul>"
        f"<h2>헤드라인</h2><ul>{hl or '<li>없음</li>'}</ul>"
        f'<p><a href="/watch">국면</a> · <a href="/news">뉴스</a></p>'
        f'<p class="disclaimer">{DISCLAIMER_KO}</p>'
    )
    body_en = sanitize_brief_html(
        f'<p class="lead">{_esc(lead)}</p>'
        f"<h2>At a glance</h2><ul>{''.join(rows_en) or '<li>Data pending</li>'}</ul>"
        f"<h2>Headlines</h2><ul>{hl or '<li>None</li>'}</ul>"
        f'<p><a href="/watch">Regime</a> · <a href="/news">News</a></p>'
        f'<p class="disclaimer">{DISCLAIMER_EN}</p>'
    )
    title_ko = f"데일리 · {hook}" if hook else f"데일리 카드 · {today}"
    title_en = f"Daily · {hook}" if hook else f"Daily card · {today}"
    return {
        "slug": slug,
        "kind": "daily",
        "date": today,
        "title": {
            "ko": title_ko[:120],
            "en": title_en[:120],
        },
        "description": {
            "ko": "국면 한눈에 + 헤드라인. 교육용.",
            "en": "Regime at a glance + headlines. Educational.",
        },
        "body": {"ko": body_ko, "en": body_en},
        "source": "daily-auto",
    }


def _esc(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def gather_daily_context() -> dict[str, Any]:
    """Pull live desk signals for the daily card (best-effort)."""
    from kostolany.connectors.news import fetch_news_desk
    from kostolany.watch_cache import read_watch_cache

    markets: list[dict[str, Any]] = []
    for sym, label in (("^GSPC", "미국 S&P 500"), ("BTC-USD", "비트코인")):
        row: dict[str, Any] = {"symbol": sym, "label": label}
        try:
            cached = read_watch_cache(sym, "momo,hmm,gbm,tsfm", 360, 2, allow_stale=True)
            if cached and cached.get("analysts"):
                # Prefer momo if present
                analysts = cached["analysts"]
                pick = next((a for a in analysts if a.get("id") == "momo"), analysts[0])
                snap = pick.get("snapshot") or {}
                row["regime"] = snap.get("regime")
                row["regime_name"] = snap.get("regime_name_ko")
                row["vote"] = snap.get("vote") or {}
                row["gauges"] = snap.get("gauges")
                row["action_ko"] = snap.get("action_ko")
        except Exception as exc:  # noqa: BLE001
            log.warning("daily context watch %s: %s", sym, exc)
        markets.append(row)

    headlines: list[dict[str, str]] = []
    priority = ""
    try:
        desk = fetch_news_desk(use_cache=True)
        # Keep enough text for markdown link cleanup; truncate after plain-ifying
        priority = str(desk.get("priority_summary_md") or "")[:2000]
        for it in (desk.get("items") or [])[:8]:
            headlines.append(
                {
                    "title": str(it.get("title") or "")[:160],
                    "theme": str(it.get("theme") or ""),
                }
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("daily context news: %s", exc)

    return {"markets": markets, "headlines": headlines, "priority_summary": priority}


def generate_and_save_daily() -> dict[str, Any]:
    ctx = gather_daily_context()
    card = build_daily_card_from_context(ctx)
    return save_brief(card)
