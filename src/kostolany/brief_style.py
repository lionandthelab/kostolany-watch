"""Shared HTML sanitizer + news-card brief helpers for weekly/daily content."""

from __future__ import annotations

import re
from typing import Any


_MD_HEADING = re.compile(r"(?m)^#{1,6}\s+(.+)$")
_CONF_PHRASE = re.compile(
    r"(?:확신도?|신뢰도|confidence|conviction)\s*"
    r"(?:는|은|이|=|:)?\s*"
    r"(?:약\s*)?(?:0?\.\d{1,2}|\d{1,3}\s*%|낮[은음]|높[은음])",
    re.I,
)
_CONF_SENTENCE = re.compile(
    r"[^.?!<\n]*(?:확신도?|신뢰도|confidence|conviction)"
    r"[^.?!<\n]*(?:0?\.\d{1,2}|\d{1,3}\s*%|25\s*%|퍼센트|mixed\s*tier)[^.?!<\n]*[.?!]",
    re.I,
)
_VOTE_META = re.compile(
    r"(?:규칙\s*)?투표는?\s*\d+\s*대\s*\d+"
    r"|티어는?\s*mixed"
    r"|\bmixed\s*(?:conviction\s*)?tier\b"
    r"|\b\d+\s*[-–]\s*\d\b(?=\s*(?:으로|로|갈|split|vote))",
    re.I,
)
_FENCE = re.compile(r"```(?:html|json|markdown)?\s*|```", re.I)
# Complete markdown links only — never span into HTML (truncated URLs are handled below)
_MD_LINK = re.compile(r"\[([^\]]{1,200})\]\((https?://[^)\s]{1,500})\)")
_MD_LINK_TRUNC = re.compile(r"\[([^\]]{1,200})\]\(https?://[^\s)<]*")
_MD_BRACKET_TAG = re.compile(r"\[([^\]\n]{1,40})\]")
_MD_BARE_URL = re.compile(r"https?://\S+")


def plain_from_markdownish(text: str) -> str:
    """Turn news-desk markdown into readable plain text for leads/titles."""
    if not text:
        return ""
    out = _FENCE.sub("", text)
    out = _MD_HEADING.sub(r"\1", out)
    out = _MD_LINK.sub(r"\1", out)
    out = _MD_LINK_TRUNC.sub(r"\1", out)
    out = _MD_BARE_URL.sub("", out)
    out = out.replace("**", "").replace("__", "").replace("#", "")
    out = _MD_BRACKET_TAG.sub(r"\1", out)
    out = re.sub(r"(?m)^\s*[-*]\s+", "", out)
    out = re.sub(r"\s+", " ", out)
    return out.strip()


def first_desk_line(md: str, *, prefer_hangul: bool = True, limit: int = 160) -> str:
    """Pick one news-desk line suitable for a card lead."""
    if not md:
        return ""
    lines: list[str] = []
    for raw in md.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        plain = plain_from_markdownish(line)
        if len(plain) < 8:
            continue
        lines.append(plain)
    if not lines:
        plain = plain_from_markdownish(md)
        return (plain[:limit] + ("…" if len(plain) > limit else "")).strip()
    pick = lines[0]
    if prefer_hangul:
        for line in lines:
            if any("\uac00" <= ch <= "\ud7a3" for ch in line):
                pick = line
                break
    if len(pick) > limit:
        return pick[: limit - 1].rstrip() + "…"
    return pick


def markdownish_to_html(text: str) -> str:
    """Convert leftover ## headings into simple HTML. Safe for already-HTML bodies."""
    if not text:
        return text
    out = _FENCE.sub("", text)

    def _h(m: re.Match[str]) -> str:
        return f"<h2>{m.group(1).strip()}</h2>"

    out = _MD_HEADING.sub(_h, out)
    out = out.replace("**", "")
    return out


def strip_confidence_noise(text: str) -> str:
    """Drop low-signal confidence % / vote-meta that reads as meaningless."""
    if not text:
        return text
    out = _CONF_SENTENCE.sub("", text)
    out = _CONF_PHRASE.sub("", out)
    out = _VOTE_META.sub("", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([.?!])", r"\1", out)
    return out


def sanitize_plain_copy(text: str) -> str:
    """Titles / deks: no markdown, no confidence hooks."""
    if not text:
        return text
    out = plain_from_markdownish(text)
    out = strip_confidence_noise(out)
    out = re.sub(
        r"(?i)(?:확신은\s*낮음|conviction\s*stays\s*low|확신도?\s*낮)",
        "",
        out,
    )
    out = re.sub(r"\s{2,}", " ", out).strip(" —–-\t ")
    return out.strip()


def sanitize_brief_html(html: str) -> str:
    """Light cleanup for brief HTML. Does not rewrite markdown links inside HTML
    (truncated markdown + HTML tags can otherwise swallow half the card)."""
    html = markdownish_to_html(html or "")
    html = strip_confidence_noise(html)
    html = re.sub(r"<p>\s*</p>", "", html)
    html = re.sub(r"(?i)<h2>\s*</h2>", "", html)
    return html.strip()


def compact_context_for_llm(ctx: dict[str, Any]) -> dict[str, Any]:
    """Keep what a news desk would use; drop raw confidence scalars."""
    markets = []
    for m in ctx.get("markets") or []:
        regime = m.get("regime")
        name = m.get("regime_name_ko") or m.get("regime_name")
        vote = m.get("vote") or {}
        if not isinstance(vote, dict):
            vote = {}
        tier = vote.get("tier")
        agree = None
        if tier in {"unanimous", "strong"}:
            agree = "합의 뚜렷"
        elif tier == "lean":
            agree = "약한 쏠림"
        elif tier == "mixed":
            agree = "의견 갈림"
        markets.append(
            {
                "label": m.get("label") or m.get("symbol"),
                "regime": regime,
                "regime_name": name,
                "agreement": agree,
                "action_ko": m.get("action_ko"),
                "gauges_note": _gauge_note(m.get("gauges")),
            }
        )

    news = ctx.get("news") or {}
    items = []
    for it in (news.get("items") or [])[:8]:
        items.append(
            {
                "title": it.get("title"),
                "theme": it.get("theme"),
                "source": it.get("source"),
            }
        )
    priority = plain_from_markdownish(str(news.get("priority_summary_md") or ""))
    priority = strip_confidence_noise(priority)[:280]

    return {
        "asof": ctx.get("asof"),
        "markets": markets,
        "news_priority": priority,
        "headlines": items,
    }


def _gauge_note(gauges: Any) -> str | None:
    if not isinstance(gauges, dict) or not gauges:
        return None
    try:
        items = [(k, float(v)) for k, v in gauges.items() if v is not None]
    except (TypeError, ValueError):
        return None
    if not items:
        return None
    items.sort(key=lambda x: x[1], reverse=True)
    hi_k, _ = items[0]
    lo_k, _ = items[-1]
    labels = {
        "volume": "거래량",
        "participation": "참여",
        "money": "유동성",
        "sentiment": "심리",
    }
    return f"상대적으로 {labels.get(hi_k, hi_k)} 강하고 {labels.get(lo_k, lo_k)} 약한 편"
