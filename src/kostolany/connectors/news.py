"""Macro desk news — RSS aggregation for rates, credit, Korea, sentiment."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from kostolany.settings import get_settings

NEWS_TTL_HOURS = 2.0
_news_refresh_lock = threading.Lock()
_news_refreshing = False

# Themes map to Kostolany gauges users already know
THEMES = {
    "money": {"label_ko": "돈·금리", "why": "유동성·기준금리·수익률 곡선 — money 게이지와 직결"},
    "credit": {"label_ko": "신용·위험", "why": "스프레드·은행·디폴트 압력 — credit 축 읽기"},
    "crypto": {"label_ko": "가상화폐", "why": "비트코인·이더·온체인·규제 — BTC-USD 국면 맥락"},
    "korea": {"label_ko": "한국 시장", "why": "한은·수급·코스피 — KS11 국면 해석의 현지 맥락"},
    "sentiment": {"label_ko": "심리·위험선호", "why": "공포·위험선호·지정학 — sentiment 게이지 보강"},
}

# Official desks first (stable anchors), then Google News RSS queries
FEEDS: list[dict[str, str]] = [
    {
        "id": "fed_press",
        "theme": "money",
        "source": "Federal Reserve",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
    },
    {
        "id": "ecb_press",
        "theme": "money",
        "source": "ECB",
        "url": "https://www.ecb.europa.eu/rss/press.html",
    },
    {
        "id": "gn_rates",
        "theme": "money",
        "source": "Google News",
        "url": (
            "https://news.google.com/rss/search?q="
            "Federal+Reserve+OR+%22interest+rate%22+OR+FOMC+OR+%22yield+curve%22"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
    },
    {
        "id": "gn_credit",
        "theme": "credit",
        "source": "Google News",
        "url": (
            "https://news.google.com/rss/search?q="
            "%22credit+spread%22+OR+%22high+yield%22+OR+bank+stress+OR+default"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
    },
    {
        "id": "gn_crypto",
        "theme": "crypto",
        "source": "Google News",
        "url": (
            "https://news.google.com/rss/search?q="
            "Bitcoin+OR+Ethereum+OR+crypto+OR+%22digital+asset%22+OR+ETF+SEC+crypto"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
    },
    {
        "id": "gn_korea",
        "theme": "korea",
        "source": "Google News",
        "url": (
            "https://news.google.com/rss/search?q="
            "%ED%95%9C%EA%B5%AD%EC%9D%80%ED%96%89+%EA%B8%B0%EC%A4%80%EA%B8%88%EB%A6%AC"
            "+OR+%EC%BD%94%EC%8A%A4%ED%94%BC+OR+%EC%99%B8%EC%9D%B8%EC%88%98%EA%B8%89"
            "&hl=ko&gl=KR&ceid=KR:ko"
        ),
    },
    {
        "id": "gn_sentiment",
        "theme": "sentiment",
        "source": "Google News",
        "url": (
            "https://news.google.com/rss/search?q="
            "VIX+OR+%22risk+off%22+OR+recession+fears+OR+geopolitical+risk"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
    },
]

DESK_LINKS: list[dict[str, str]] = [
    {
        "title": "FOMC 일정·성명 (Federal Reserve)",
        "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        "theme": "money",
        "source": "Federal Reserve",
    },
    {
        "title": "한국은행 보도자료",
        "url": "https://www.bok.or.kr/portal/bbs/B0000188/list.do?menuNo=200759",
        "theme": "korea",
        "source": "Bank of Korea",
    },
    {
        "title": "FRED 거시 대시보드",
        "url": "https://fred.stlouisfed.org/",
        "theme": "money",
        "source": "FRED",
    },
    {
        "title": "CoinDesk Markets",
        "url": "https://www.coindesk.com/markets/",
        "theme": "crypto",
        "source": "CoinDesk",
    },
    {
        "title": "CBOE VIX",
        "url": "https://www.cboe.com/tradable_products/vix/",
        "theme": "sentiment",
        "source": "Cboe",
    },
]

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "media": "http://search.yahoo.com/mrss/",
}


def _cache_path():
    return get_settings().cache_path / "news_desk.json"


def _strip_html(text: str) -> str:
    t = re.sub(r"<[^>]+>", " ", text or "")
    t = unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _parse_time(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).timestamp()
    except Exception:  # noqa: BLE001
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:  # noqa: BLE001
        return None


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _google_news_destination(link: str) -> str:
    """Unwrap Google News redirect when possible."""
    try:
        q = parse_qs(urlparse(link).query)
        if "url" in q and q["url"]:
            return unquote(q["url"][0])
    except Exception:  # noqa: BLE001
        pass
    return link


def _text(el: ET.Element | None) -> str:
    if el is None or el.text is None:
        return ""
    return el.text.strip()


def _find(el: ET.Element, *names: str) -> ET.Element | None:
    for name in names:
        hit = el.find(name)
        if hit is not None:
            return hit
        if "}" not in name:
            for child in el:
                tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                if tag == name:
                    return child
    return None


def _clean_headline(title: str, url: str, summary: str = "") -> str:
    """Avoid raw Google News article URLs as visible titles (breaks layout)."""
    t = (title or "").strip()
    if not t:
        t = (summary or "").strip()[:120]
    low = t.lower()
    if low.startswith("http") or "news.google.com/rss/articles" in low or "news.google.com/articles" in low:
        host = urlparse(url).netloc.replace("www.", "") if url else ""
        if host and "news.google" not in host:
            return f"{host} 헤드라인"
        bit = (summary or "").strip()
        if bit and not bit.lower().startswith("http"):
            return bit[:100] + ("…" if len(bit) > 100 else "")
        return "헤드라인"
    if len(t) > 180:
        return t[:177].rstrip() + "…"
    return t


def _parse_rss_items(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, Any]] = []

    # RSS 2.0
    for item in root.findall(".//item"):
        title = _strip_html(_text(_find(item, "title")))
        link = _text(_find(item, "link")) or ""
        if not link:
            guid = _find(item, "guid")
            if guid is not None and (guid.get("isPermaLink") == "true" or _text(guid).startswith("http")):
                link = _text(guid)
        desc = _strip_html(_text(_find(item, "description")))
        pub = _text(_find(item, "pubDate"))
        source_el = _find(item, "source")
        source = _text(source_el) if source_el is not None else ""
        url = _google_news_destination(link) if link else ""
        title = _clean_headline(title, url, desc)
        if title and url:
            items.append(
                {
                    "title": title,
                    "url": url,
                    "summary": desc[:280] if desc else "",
                    "published_at": _iso(_parse_time(pub)),
                    "published_epoch": _parse_time(pub) or 0.0,
                    "source_hint": source,
                }
            )

    # Atom
    for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        title = _strip_html(_text(entry.find("{http://www.w3.org/2005/Atom}title")))
        link = ""
        for ln in entry.findall("{http://www.w3.org/2005/Atom}link"):
            href = ln.get("href") or ""
            if ln.get("rel") in (None, "alternate") and href:
                link = href
                break
        summary_el = entry.find("{http://www.w3.org/2005/Atom}summary")
        if summary_el is None:
            summary_el = entry.find("{http://www.w3.org/2005/Atom}content")
        desc = _strip_html(_text(summary_el))
        updated = _text(entry.find("{http://www.w3.org/2005/Atom}updated")) or _text(
            entry.find("{http://www.w3.org/2005/Atom}published")
        )
        title = _clean_headline(title, link, desc)
        if title and link:
            items.append(
                {
                    "title": title,
                    "url": link,
                    "summary": desc[:280] if desc else "",
                    "published_at": _iso(_parse_time(updated)),
                    "published_epoch": _parse_time(updated) or 0.0,
                    "source_hint": "",
                }
            )

    return items


def _fetch_feed(feed: dict[str, str], client: httpx.Client) -> list[dict[str, Any]]:
    try:
        r = client.get(feed["url"], follow_redirects=True)
        r.raise_for_status()
        raw_items = _parse_rss_items(r.text)
    except Exception:  # noqa: BLE001
        return []

    theme = feed["theme"]
    meta = THEMES[theme]
    out = []
    for it in raw_items[:8]:
        uid = hashlib.sha1(f"{it['url']}|{it['title']}".encode()).hexdigest()[:12]
        out.append(
            {
                "id": uid,
                "title": it["title"],
                "url": it["url"],
                "summary": it["summary"],
                "source": it["source_hint"] or feed["source"],
                "theme": theme,
                "theme_ko": meta["label_ko"],
                "why": meta["why"],
                "published_at": it["published_at"],
                "published_epoch": it["published_epoch"],
                "feed_id": feed["id"],
            }
        )
    return out


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in sorted(items, key=lambda x: x.get("published_epoch") or 0, reverse=True):
        key = re.sub(r"\W+", "", it["title"].lower())[:48]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


_FEED_PRIORITY = {
    "fed_press": 100,
    "ecb_press": 90,
    "gn_rates": 70,
    "gn_credit": 65,
    "gn_crypto": 68,
    "gn_korea": 75,
    "gn_sentiment": 55,
}

_THEME_PRIORITY = {
    "money": 40,
    "credit": 30,
    "crypto": 32,
    "korea": 35,
    "sentiment": 15,
}


def _item_importance(it: dict[str, Any]) -> float:
    score = float(_FEED_PRIORITY.get(str(it.get("feed_id") or ""), 40))
    score += float(_THEME_PRIORITY.get(str(it.get("theme") or ""), 0))
    epoch = float(it.get("published_epoch") or 0)
    if epoch > 0:
        age_h = max(0.0, (time.time() - epoch) / 3600.0)
        score += max(0.0, 24.0 - age_h)  # fresher within ~1d
    title = str(it.get("title") or "").lower()
    for kw, bump in (
        ("rate", 8),
        ("fomc", 12),
        ("fed", 6),
        ("금리", 10),
        ("한은", 10),
        ("default", 8),
        ("spread", 6),
        ("bitcoin", 10),
        ("btc", 8),
        ("ethereum", 8),
        ("crypto", 8),
        ("etf", 5),
        ("비트코인", 10),
        ("가상화폐", 8),
        ("암호화폐", 8),
        ("recession", 7),
        ("전쟁", 6),
        ("war", 5),
    ):
        if kw in title:
            score += bump
    return score


# Lightweight lexical tone — not NLP; enough for a desk meter.
_TONE_BULL = (
    "rally",
    "surge",
    "soar",
    "ease",
    "easing",
    "cut rates",
    "rate cut",
    "dovish",
    "risk-on",
    "risk on",
    "rebound",
    "인하",
    "완화",
    "상승",
    "호조",
    "강세",
    "반등",
    "유동성",
)
_TONE_BEAR = (
    "hike",
    "hawkish",
    "selloff",
    "sell-off",
    "recession",
    "default",
    "crash",
    "fear",
    "risk-off",
    "risk off",
    "war",
    "stress",
    "tightening",
    "인상",
    "긴축",
    "침체",
    "하락",
    "약세",
    "공포",
    "전쟁",
    "디폴트",
    "부실",
)


def _tone_hits(text: str) -> tuple[int, int]:
    low = text.lower()
    bull = sum(1 for kw in _TONE_BULL if kw in low)
    bear = sum(1 for kw in _TONE_BEAR if kw in low)
    return bull, bear


def score_section_tone(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Return -1..+1 tone score plus a short Korean label."""
    bull = bear = 0
    for it in items:
        b, r = _tone_hits(f"{it.get('title') or ''} {it.get('summary') or ''}")
        bull += b
        bear += r
    total = bull + bear
    if total == 0:
        score = 0.0
    else:
        score = (bull - bear) / float(total)
    score = float(max(-1.0, min(1.0, score)))
    if score >= 0.35:
        label = "완화·위험선호 쪽"
    elif score <= -0.35:
        label = "경계·위험회피 쪽"
    elif abs(score) < 0.12:
        label = "혼조·중립"
    elif score > 0:
        label = "약한 완화 기조"
    else:
        label = "약한 경계 기조"
    return {
        "score": round(score, 3),
        "label": label,
        "bull": bull,
        "bear": bear,
        "n": len(items),
    }


def build_section_summary_ko(items: list[dict[str, Any]], *, label_ko: str, why: str) -> str:
    """One short prose line from top headlines (no LLM)."""
    if not items:
        return f"{label_ko}: 수집된 헤드라인이 없습니다."
    ranked = sorted(items, key=_item_importance, reverse=True)[:3]
    titles = [str(it.get("title") or "").strip() for it in ranked if str(it.get("title") or "").strip()]
    if not titles:
        return f"{label_ko}: {why}"
    joined = " · ".join(titles)
    if len(joined) > 150:
        joined = joined[:147].rstrip() + "…"
    return f"{why} 지금 눈에 띄는 소식은 {joined}."


def enrich_section(theme: str, meta: dict[str, str], items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "theme": theme,
        "label_ko": meta["label_ko"],
        "why": meta["why"],
        "summary_ko": build_section_summary_ko(items, label_ko=meta["label_ko"], why=meta["why"]),
        "tone": score_section_tone(items),
        "items": items,
    }


def build_priority_summary_md(items: list[dict[str, Any]], *, limit: int = 8) -> str:
    """Bullet briefing: importance-ranked, max 2 per theme."""
    ranked = sorted(items, key=_item_importance, reverse=True)
    picked: list[dict[str, Any]] = []
    theme_counts: dict[str, int] = {}
    for it in ranked:
        theme = str(it.get("theme") or "other")
        if theme_counts.get(theme, 0) >= 2:
            continue
        picked.append(it)
        theme_counts[theme] = theme_counts.get(theme, 0) + 1
        if len(picked) >= limit:
            break

    lines = ["## 오늘 핵심", ""]
    if not picked:
        lines.append("- 수집된 헤드라인이 없습니다. 새로고침을 시도하세요.")
        return "\n".join(lines)

    for it in picked:
        theme_ko = it.get("theme_ko") or THEMES.get(str(it.get("theme")), {}).get("label_ko", "")
        title = _clean_headline(str(it.get("title") or ""), str(it.get("url") or ""), str(it.get("summary") or ""))
        source = str(it.get("source") or "").strip()
        url = str(it.get("url") or "").strip()
        bit = f"- **[{theme_ko}]** {source} — [{title}]({url})" if url else f"- **[{theme_ko}]** {source} — {title}"
        lines.append(bit)
    return "\n".join(lines)


def _read_news_cache() -> dict[str, Any] | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(cached, dict) or not cached.get("items"):
        return None
    age_h = (time.time() - path.stat().st_mtime) / 3600.0
    out = dict(cached)
    out["cached"] = True
    out["stale"] = age_h > NEWS_TTL_HOURS
    out["cache_age_hours"] = round(age_h, 3)
    if not out.get("priority_summary_md"):
        out["priority_summary_md"] = build_priority_summary_md(out["items"])
    else:
        # Rebuild brief so cached URL-as-title rows cannot blow the layout.
        out["priority_summary_md"] = build_priority_summary_md(out["items"])
    # Backfill section briefs/tone for older caches
    sections = out.get("sections")
    if isinstance(sections, list):
        fixed: list[dict[str, Any]] = []
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            items = list(sec.get("items") or [])
            theme = str(sec.get("theme") or "")
            meta = THEMES.get(theme, {"label_ko": sec.get("label_ko") or theme, "why": sec.get("why") or ""})
            if not sec.get("summary_ko") or not isinstance(sec.get("tone"), dict):
                enriched = enrich_section(theme, meta, items)
                sec = {**sec, "summary_ko": enriched["summary_ko"], "tone": enriched["tone"]}
            fixed.append(sec)
        out["sections"] = fixed
    return out


def _schedule_news_rebuild(per_theme: int = 6) -> bool:
    global _news_refreshing
    with _news_refresh_lock:
        if _news_refreshing:
            return False
        _news_refreshing = True

    def _run() -> None:
        global _news_refreshing
        try:
            _compute_news_desk(per_theme=per_theme)
        finally:
            with _news_refresh_lock:
                _news_refreshing = False

    threading.Thread(target=_run, name="news-refresh", daemon=True).start()
    return True


def fetch_news_desk(*, use_cache: bool = True, refresh: bool = False, per_theme: int = 6) -> dict[str, Any]:
    cached = _read_news_cache() if use_cache or refresh else None

    if refresh:
        if cached is not None:
            started = _schedule_news_rebuild(per_theme=per_theme)
            out = dict(cached)
            out["refreshing"] = True
            out["refresh_started"] = started
            return out
        return _compute_news_desk(per_theme=per_theme)

    if use_cache and cached is not None:
        if cached.get("stale"):
            _schedule_news_rebuild(per_theme=per_theme)
            cached["refreshing"] = True
        else:
            cached["refreshing"] = _news_refreshing
        return cached

    return _compute_news_desk(per_theme=per_theme)


def _compute_news_desk(*, per_theme: int = 6) -> dict[str, Any]:
    path = _cache_path()
    timeout = get_settings().http_timeout
    collected: list[dict[str, Any]] = []
    with httpx.Client(
        timeout=timeout,
        headers={"User-Agent": "KostolanyWatch/0.2 (+research; macro-desk)"},
    ) as client:
        for feed in FEEDS:
            collected.extend(_fetch_feed(feed, client))

    items = _dedupe(collected)
    by_theme: dict[str, list[dict[str, Any]]] = {k: [] for k in THEMES}
    for it in items:
        bucket = by_theme.get(it["theme"])
        if bucket is not None and len(bucket) < per_theme:
            bucket.append(it)

    brief_pool = [it for theme_items in by_theme.values() for it in theme_items]
    priority_md = build_priority_summary_md(brief_pool)

    def _public_item(it: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in it.items() if k not in ("published_epoch", "feed_id")}

    sections = [
        enrich_section(key, meta, [_public_item(it) for it in by_theme[key]])
        for key, meta in THEMES.items()
    ]

    payload = {
        "asof": _iso(time.time()),
        "cached": False,
        "stale": False,
        "refreshing": False,
        "ttl_hours": NEWS_TTL_HOURS,
        "desk_links": DESK_LINKS,
        "sections": sections,
        "items": [it for sec in sections for it in sec["items"]],
        "priority_summary_md": priority_md,
        "disclaimer": "참고용 헤드라인입니다. 투자 권유가 아닙니다.",
    }
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return payload
