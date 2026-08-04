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
    # /guide is prerendered separately by build-guide.mjs (real article HTML).
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
    """Output must be `<route>.html`; a per-route directory brings back the 301."""
    script = PRERENDER.read_text(encoding="utf-8")
    writes = re.findall(r"writeFileSync\(\s*([^,]+),", script)
    assert writes, "no writeFileSync found — did the script change shape?"
    for target in writes:
        assert "index.html" not in target, f"writes a directory index: {target.strip()}"
    assert re.search(r'`\$\{route\.path\.replace\([^)]*\)\}\.html`', script), (
        "route shells are no longer named <route>.html"
    )


def test_postbuild_hook_is_wired():
    pkg = (ROOT / "web" / "package.json").read_text(encoding="utf-8")
    assert "prerender-routes.mjs" in pkg, "prerender step is not run by the build"
    assert '"postbuild"' in pkg, "prerender must run after vite build (needs hashed assets)"
