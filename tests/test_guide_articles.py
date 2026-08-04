"""Guide articles are the only pages a non-rendering crawler can actually read.

`build-guide.mjs` turns each entry in articles.json into a standalone prerendered
HTML page plus a sitemap and RSS entry, so a defect here ships straight to search
engines rather than being caught at runtime.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ARTICLES = ROOT / "web" / "src" / "guide" / "articles.json"

DATA = json.loads(ARTICLES.read_text(encoding="utf-8"))
PUBLISHED = [a for a in DATA if a.get("status") != "draft"]
SLUGS = {a["slug"] for a in PUBLISHED}

#: Routes the SPA serves, so an internal link to one of them is valid.
APP_ROUTES = {"/", "/watch", "/macro", "/news", "/about", "/guide/", "/guide"}


def _ids(articles):
    return [a["slug"] for a in articles]


@pytest.mark.parametrize("article", PUBLISHED, ids=_ids(PUBLISHED))
def test_article_has_both_locales(article):
    for field in ("title", "description", "body"):
        assert article[field].get("ko"), f"{article['slug']}: missing ko {field}"
        assert article[field].get("en"), f"{article['slug']}: missing en {field}"


@pytest.mark.parametrize("article", PUBLISHED, ids=_ids(PUBLISHED))
def test_article_slug_is_url_safe(article):
    assert re.fullmatch(r"[a-z0-9][a-z0-9-]{2,60}", article["slug"])


@pytest.mark.parametrize("article", PUBLISHED, ids=_ids(PUBLISHED))
def test_article_carries_a_disclaimer(article):
    """Every user-facing surface keeps the disclaimer (AGENTS.md non-negotiable 3)."""
    assert "투자 권유" in article["body"]["ko"]
    assert "not investment advice" in article["body"]["en"].lower()


@pytest.mark.parametrize("article", PUBLISHED, ids=_ids(PUBLISHED))
def test_internal_links_resolve(article):
    """A dead /guide/<slug> link is a 404 served to a crawler that followed it."""
    for lang in ("ko", "en"):
        for href in re.findall(r'href="(/[^"]*)"', article["body"][lang]):
            if href in APP_ROUTES:
                continue
            m = re.fullmatch(r"/guide/([a-z0-9-]+)/?", href)
            assert m, f"{article['slug']} ({lang}): unexpected internal link {href}"
            assert m.group(1) in SLUGS, (
                f"{article['slug']} ({lang}): links to missing article {href}"
            )


@pytest.mark.parametrize("article", PUBLISHED, ids=_ids(PUBLISHED))
def test_body_is_substantial_enough_to_index(article):
    """Thin pages do not get indexed; they get classified as thin content."""
    text = re.sub(r"<[^>]+>", "", article["body"]["ko"])
    assert len(text) >= 500, f"{article['slug']}: only {len(text)} chars of Korean body"


@pytest.mark.parametrize("article", PUBLISHED, ids=_ids(PUBLISHED))
def test_description_fits_a_search_snippet(article):
    for lang in ("ko", "en"):
        assert 40 <= len(article["description"][lang]) <= 320, (
            f"{article['slug']} ({lang}): description length {len(article['description'][lang])}"
        )


def test_titles_and_slugs_are_unique():
    slugs = [a["slug"] for a in DATA]
    assert len(slugs) == len(set(slugs)), "duplicate slug"
    titles = [a["title"]["ko"] for a in PUBLISHED]
    assert len(titles) == len(set(titles)), "duplicate Korean title — self-competing pages"


def test_no_measured_hit_rate_is_quoted_in_prose():
    """Articles are static; a measured number here would drift from the artifact."""
    for article in PUBLISHED:
        for lang in ("ko", "en"):
            body = article["body"][lang]
            assert not re.search(r"\d+\s*%", body), (
                f"{article['slug']} ({lang}): hardcoded percentage in article prose"
            )
