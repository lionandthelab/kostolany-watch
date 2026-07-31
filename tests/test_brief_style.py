from __future__ import annotations

from kostolany.brief_style import (
    compact_context_for_llm,
    sanitize_brief_html,
    sanitize_plain_copy,
)


def test_plain_from_markdownish_strips_links() -> None:
    from kostolany.brief_style import plain_from_markdownish

    raw = "오늘 핵심 - [이코노미스트](https://example.com/x) — 금리 동결"
    out = plain_from_markdownish(raw)
    assert "http" not in out
    assert "[" not in out
    assert "이코노미스트" in out
    assert "금리 동결" in out


def test_sanitize_does_not_eat_html_via_truncated_md_link() -> None:
    """Truncated markdown URL must not swallow following HTML via (하락)."""
    broken_lead = (
        '<p class="lead">오늘 핵심 — '
        "[美 FOMC](https://news.google.com/rss/articles/CBMiTRUNC"
        "</p><h2>한눈에</h2>"
        "<ul><li><strong>미국</strong> — B2 · 동행(하락) · 의견 갈림</li>"
        "<li><strong>비트코인</strong> — B2</li></ul>"
    )
    # Lead should be cleaned BEFORE it enters HTML; sanitize must not destroy lists
    out = sanitize_brief_html(broken_lead)
    assert "<li><strong>미국</strong>" in out
    assert "비트코인" in out


def test_sanitize_strips_markdown_headings_and_confidence() -> None:
    raw = """## 한눈에
<p>확신도는 0.25 수준입니다. S&P는 B3입니다.</p>
**강조**
"""
    out = sanitize_brief_html(raw)
    assert "##" not in out
    assert "**" not in out
    assert "0.25" not in out
    assert "확신" not in out
    assert "<h2>" in out
    assert "B3" in out


def test_sanitize_title_drops_low_conviction_hook() -> None:
    t = sanitize_plain_copy("주간 브리핑 #1 — 두 시장 아래쪽, 그러나 확신은 낮음")
    assert "확신" not in t
    assert "주간 브리핑" in t


def test_daily_card_omits_confidence_percent() -> None:
    from kostolany import briefs

    card = briefs.build_daily_card_from_context(
        {
            "markets": [
                {
                    "label": "미국",
                    "regime": "B3",
                    "regime_name": "과장·하락",
                    "confidence": 0.25,
                    "vote": {"tier": "mixed", "side": "down"},
                    "gauges": {"money": 0.7, "sentiment": 0.3, "volume": 0.5, "participation": 0.5},
                }
            ],
            "headlines": [{"title": "Fed holds rates", "theme": "money"}],
            "priority_summary": "## Priority\nRates held; credit stress in focus.",
        }
    )
    body = card["body"]["ko"]
    assert "확신" not in body
    assert "25%" not in body
    assert "0.25" not in body
    assert "B3" in body
    assert "의견 갈림" in body
    assert "##" not in body


def test_compact_context_drops_confidence() -> None:
    slim = compact_context_for_llm(
        {
            "markets": [
                {
                    "label": "US",
                    "regime": "B3",
                    "regime_name_ko": "과장·하락",
                    "confidence": 0.25,
                    "vote": {"tier": "mixed"},
                    "gauges": {"money": 0.8, "sentiment": 0.2},
                }
            ],
            "news": {"priority_summary_md": "## Hi\nHello", "items": [{"title": "A", "theme": "x"}]},
        }
    )
    assert "confidence" not in slim["markets"][0]
    assert slim["markets"][0]["agreement"] == "의견 갈림"
    assert "#" not in slim["news_priority"]
