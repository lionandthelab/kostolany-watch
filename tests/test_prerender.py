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


def test_static_shell_wins_over_the_catch_all_rewrite():
    """The shells only work because Hosting prefers a matching static file."""
    fb = FIREBASE.read_text(encoding="utf-8")
    assert '"source": "**"' in fb, "catch-all rewrite disappeared — re-check shell serving"
    assert '"public": "web/dist"' in fb or '"public"' in fb


def test_postbuild_hook_is_wired():
    pkg = (ROOT / "web" / "package.json").read_text(encoding="utf-8")
    assert "prerender-routes.mjs" in pkg, "prerender step is not run by the build"
    assert '"postbuild"' in pkg, "prerender must run after vite build (needs hashed assets)"
