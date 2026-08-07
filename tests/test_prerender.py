"""Per-route prerender shells must not drift from the app's own SEO copy.

`firebase.json` rewrites `**` to `/index.html`, whose head hardcodes the home
title and `canonical="…/"`. Without a static shell per route, a non-rendering
crawler sees every route as a duplicate of the home page — `seo.ts` only fixes
it after React hydrates.

The route copy lives twice (TypeScript for the app, JS for the build script,
which cannot import TS). These tests are what keeps the copies identical.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KO_TS = ROOT / "web" / "src" / "i18n" / "ko.ts"
PRERENDER = ROOT / "web" / "scripts" / "prerender-routes.mjs"
FIREBASE = ROOT / "firebase.json"

ROUTES = ("watch", "macro", "news", "about")


def _seo_from_ko_ts() -> dict[str, dict[str, str]]:
    """Pull `seo: { <route>: { title, description } }` out of the i18n source."""
    text = KO_TS.read_text(encoding="utf-8")
    seo = text[text.index("  seo: {") :]
    out: dict[str, dict[str, str]] = {}
    for route in ROUTES:
        block = seo[seo.index(f"    {route}: {{") :]
        title = re.search(r'title:\s*"((?:[^"\\]|\\.)*)"', block)
        desc = re.search(r'description:\s*\n?\s*"((?:[^"\\]|\\.)*)"', block)
        assert title and desc, f"could not parse seo.{route} from ko.ts"
        out[route] = {"title": title.group(1), "description": desc.group(1)}
    return out


def _routes_from_script() -> dict[str, dict[str, str]]:
    text = PRERENDER.read_text(encoding="utf-8")
    body = text[text.index("const ROUTES = [") : text.index("const SITE_NAME")]
    out: dict[str, dict[str, str]] = {}
    for chunk in body.split("path:")[1:]:
        path = re.search(r'"/([a-z]+)"', chunk).group(1)
        title = re.search(r'title:\s*"((?:[^"\\]|\\.)*)"', chunk).group(1)
        desc = re.search(r'description:\s*\n?\s*"((?:[^"\\]|\\.)*)"', chunk).group(1)
        out[path] = {"title": title, "description": desc}
    return out


@pytest.mark.parametrize("route", ROUTES)
def test_prerender_copy_matches_app_seo(route):
    """Two copies of the same string; a diff here is a live SEO defect."""
    app = _seo_from_ko_ts()[route]
    script = _routes_from_script()[route]
    assert script["title"] == app["title"], f"seo.{route}.title drifted from ko.ts"
    assert script["description"] == app["description"], (
        f"seo.{route}.description drifted from ko.ts"
    )


def test_every_spa_route_is_prerendered():
    """Any route in the SPA router needs a shell, or it inherits the home meta."""
    app_tsx = (ROOT / "web" / "src" / "App.tsx").read_text(encoding="utf-8")
    routed = set(re.findall(r'if \(path === "/([a-z]+)"\)', app_tsx))
    # /guide is prerendered separately: build-guide.mjs writes the article HTML
    # and prerender-routes.mjs writes the /guide/ hub as a directory index,
    # because the guide's canonical URL ends in a slash. See the hub tests below.
    routed.discard("guide")
    missing = routed - set(_routes_from_script())
    assert not missing, f"SPA routes without a prerendered shell: {sorted(missing)}"


@pytest.mark.parametrize("route", ROUTES)
def test_route_rewrite_points_at_its_shell(route):
    """Each route needs an explicit rewrite ahead of the catch-all.

    A `<route>/index.html` directory instead makes Hosting 301 `/watch` to
    `/watch/`, while the sitemap, the guide nav and the SPA router all emit the
    slash-less form — every internal link would take a redirect hop and the
    canonical would disagree with the served URL.
    """
    fb = FIREBASE.read_text(encoding="utf-8")
    assert f'"source": "/{route}"' in fb, f"/{route} has no rewrite — falls through to catch-all"
    assert f'"destination": "/{route}.html"' in fb

    catch_all = fb.index('"source": "**"')
    assert fb.index(f'"source": "/{route}"') < catch_all, (
        f"/{route} rewrite must precede the catch-all — first match wins"
    )


def test_prerender_emits_flat_html_not_directories():
    """SPA route shells must be `<route>.html`; a directory brings back the 301.

    /guide/ and /guide/<slug>/ are deliberately NOT covered by this rule: their
    canonical URLs end in a slash (sitemap, feed.xml, every in-article nav
    link), so for them the directory index is the form that serves with no
    redirect hop, and a flat `guide.html` would make Hosting 301 /guide/ →
    /guide. The rule being enforced is about the four slash-less SPA routes.

    The previous form of this test matched `writeFileSync\\(\\s*([^,]+),`, which
    stops at the first comma and so only ever saw `join(dist` — it would have
    passed for `join(dist, "watch", "index.html")` too. Stated directly now.
    """
    script = PRERENDER.read_text(encoding="utf-8")
    targets = _write_targets(script)
    assert targets, "no writeFileSync found — did the script change shape?"
    for target in targets:
        if "index.html" not in target:
            continue  # flat file — the rule this test exists for
        for route in ROUTES:
            assert route not in target, f"/{route} is written as a directory index: {target}"
    assert re.search(r'`\$\{route\.path\.replace\([^)]*\)\}\.html`', script), (
        "route shells are no longer named <route>.html"
    )


def _write_targets(script: str) -> list[str]:
    """First argument of every `writeFileSync(...)` call, parens balanced.

    Matching with a plain regex reads the script's own prose comments as if
    they were code — `[^,]+` also stops at the first comma, so it never saw
    past `join(dist`. Scan the call sites instead.
    """
    out: list[str] = []
    for m in re.finditer(r"writeFileSync\(", script):
        i, depth = m.end(), 1
        start = i
        while i < len(script) and depth:
            if script[i] == "(":
                depth += 1
            elif script[i] == ")":
                depth -= 1
            elif script[i] == "," and depth == 1:
                break
            i += 1
        out.append(script[start:i].strip())
    return out


def test_no_measured_value_is_baked_into_the_prerender():
    """Static HTML must never carry a measured number.

    Every hit rate on screen has exactly one source: the calibration artifact →
    calibration.py → payload → pctFloor (spec §0.1). A value copied into a build
    script becomes a second source that cannot be re-measured and will silently
    drift from the artifact. Evergreen prose only.
    """
    from kostolany.calibration import CONFIDENCE_VIEW_BY_SYMBOL, MEASURED_BY_SYMBOL

    script = PRERENDER.read_text(encoding="utf-8")
    # Whole file, not just the ROUTES block. The guide hub, the scheduled-slug
    # cover and the landing body were added later and all emit user-facing prose
    # outside that window; scoping the scan to ROUTES let ~140 lines of copy in
    # through the side door. The file carries no percent sign of any kind today
    # (no CSS widths either), so banning the character outright costs nothing and
    # cannot be outgrown by the next renderer someone appends.
    assert "%" not in script, (
        "prerender copy contains a percent sign — measured numbers belong in the payload"
    )

    measured: set[int] = set()
    for view in CONFIDENCE_VIEW_BY_SYMBOL.values():
        for cell in view["menu"].values():
            if isinstance(cell, (int, float)):
                measured.add(int(cell * 100))
        for tier in view["tiers"].values():
            measured.update(int(v * 100) for v in tier.values() if isinstance(v, (int, float)))
    for block in MEASURED_BY_SYMBOL.values():
        for arm in block["measured"].values():
            measured.update(int(v * 100) for v in arm.values() if isinstance(v, (int, float)))

    for value in sorted(v for v in measured if v >= 10):
        assert f"{value}%" not in script, f"measured value {value}% is hardcoded in the prerender"


def test_route_shells_carry_real_body_copy():
    """The whole point of the prerender: crawlers that skip JS get text."""
    script = PRERENDER.read_text(encoding="utf-8")
    assert 'id="root"' in script and "renderIntro" in script, "intro body is not rendered"
    body = script[script.index("const ROUTES") : script.index("const SITE_NAME")]
    for route in ROUTES:
        chunk = body[body.index(f'"/{route}"') :]
        chunk = chunk[: chunk.index("},\n  {")] if "},\n  {" in chunk else chunk
        assert "h1:" in chunk, f"/{route} has no h1 for crawlers"
        assert "intro:" in chunk, f"/{route} has no intro copy"


def test_postbuild_hook_is_wired():
    pkg = (ROOT / "web" / "package.json").read_text(encoding="utf-8")
    assert "prerender-routes.mjs" in pkg, "prerender step is not run by the build"
    assert '"postbuild"' in pkg, "prerender must run after vite build (needs hashed assets)"


# —— /guide/ hub, soft-404 covers, landing body ——————————————————————————
#
# Until 2026-08-07 the hub at /guide/ was the home shell with an empty #root:
# zero links to any of the nine live articles, so sitemap.xml was their only
# crawl entry point, and the landing page linked to the guide with a <button>,
# which no crawler follows.

BUILD_GUIDE = ROOT / "web" / "scripts" / "build-guide.mjs"


def test_guide_hub_is_generated_from_the_build_gate():
    """One gate, not two — the hub must not re-derive which articles are live.

    A second copy of the date gate is how the hub ends up advertising a slug
    whose HTML `build-guide.mjs` withheld, i.e. a link straight to a soft 404.
    """
    script = PRERENDER.read_text(encoding="utf-8")
    assert re.search(
        r'import \{[^}]*liveArticles[^}]*\} from "\./build-guide\.mjs"', script
    ), "prerender does not import the live-article list from build-guide.mjs"
    assert "renderGuideHub" in script, "no /guide/ hub is generated"
    # The gate itself must stay in build-guide.mjs (test_scheduled_publish pins
    # its text there); prerender must not carry its own copy.
    assert 'status !== "draft"' not in script, "prerender duplicates the publish gate"


def test_guide_hub_seo_matches_app_copy():
    """Same drift rule as the SPA routes: the hub head is written twice."""
    app = _seo_from_ko_ts_guide()
    script = PRERENDER.read_text(encoding="utf-8")
    block = script[script.index("const GUIDE_SEO") :]
    block = block[: block.index("}")]
    title = re.search(r'title:\s*"((?:[^"\\]|\\.)*)"', block).group(1)
    desc = re.search(r'description:\s*\n?\s*"((?:[^"\\]|\\.)*)"', block).group(1)
    assert title == app["title"], "GUIDE_SEO.title drifted from ko.ts seo.guide"
    assert desc == app["description"], "GUIDE_SEO.description drifted from ko.ts seo.guide"


def _seo_from_ko_ts_guide() -> dict[str, str]:
    text = KO_TS.read_text(encoding="utf-8")
    block = text[text.index("  seo: {") :]
    block = block[block.index("    guide: {") :]
    title = re.search(r'title:\s*"((?:[^"\\]|\\.)*)"', block)
    desc = re.search(r'description:\s*\n?\s*"((?:[^"\\]|\\.)*)"', block)
    assert title and desc, "could not parse seo.guide from ko.ts"
    return {"title": title.group(1), "description": desc.group(1)}


def test_scheduled_slugs_get_a_static_noindex():
    """Client-side noindex arrives a render pass too late for a crawler.

    A slug whose date has not come serves 200 via the catch-all rewrite with
    the home shell's `index,follow` head. `applySeo` only corrects that after
    React mounts — measured live 2026-08-07 on /guide/backtest-traps/: 200,
    index,follow, 44 characters of body. The directive has to be in the bytes.
    """
    script = PRERENDER.read_text(encoding="utf-8")
    assert "scheduledArticles" in script, "scheduled slugs get no static cover"
    assert '"noindex,follow"' in script, "the cover does not carry noindex"
    build = BUILD_GUIDE.read_text(encoding="utf-8")
    assert "export function scheduledArticles" in build


def test_scheduled_cover_leaks_no_article_text():
    """The cover must not become a back door around the publish date gate."""
    script = PRERENDER.read_text(encoding="utf-8")
    body = script[script.index("function renderScheduledCover") :]
    body = body[: body.index("\nfunction ")]
    for field in ("article.title", "article.body", "article.description", "article.date"):
        assert field not in body, f"scheduled cover renders {field} before its publish date"


def test_landing_shell_links_the_guide_hub():
    """`/` is the site's most-linked page; the hub needs an inbound link from it."""
    script = PRERENDER.read_text(encoding="utf-8")
    assert "renderLanding" in script, "the landing has no crawlable body"
    body = script[script.index("function renderLanding") :]
    body = body[: body.index("\nfunction ")]
    assert 'href="/guide/"' in body, "landing shell does not link /guide/"

    landing = (ROOT / "web" / "src" / "Landing.tsx").read_text(encoding="utf-8")
    assert 'href="/guide/"' in landing, (
        "the React landing still reaches the guide by <button> only — not a followable link"
    )
