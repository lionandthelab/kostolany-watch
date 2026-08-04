"""Scheduled guide articles must be withheld until their date arrives.

The gate lives in `web/scripts/build-guide.mjs`: an article that is not live
gets no HTML file, no sitemap entry and no RSS item, so its URL does not exist.
Hiding it from the list page alone would not be enough — the sitemap would
advertise it and the direct URL would still serve it.

`web/src/guide/catalog.ts` carries a deliberate copy of the same rule so a
cached bundle cannot link to a page that has not been generated yet. These
tests pin both, and pin that they agree.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
ARTICLES = WEB / "src" / "guide" / "articles.json"
BUILD_GUIDE = WEB / "scripts" / "build-guide.mjs"
CATALOG = WEB / "src" / "guide" / "catalog.ts"

DATA = json.loads(ARTICLES.read_text(encoding="utf-8"))
NODE = shutil.which("node")


def _run_build(as_of: str):
    """Build the guide as if it were `as_of`, returning (stdout, sitemap text)."""
    proc = subprocess.run(
        [NODE, "scripts/build-guide.mjs"],
        cwd=WEB,
        env={**__import__("os").environ, "PUBLISH_AS_OF": as_of},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    sitemap = (WEB / "public" / "sitemap.xml").read_text(encoding="utf-8")
    return proc.stdout, sitemap


@pytest.fixture(scope="module", autouse=True)
def _restore_build():
    """Leave the tree built for the REAL today, whatever the tests simulated.

    Restoring to the newest article's date instead would generate every future
    post — which is precisely the state this feature exists to prevent, and it
    would be committed on the next `git add`.
    """
    yield
    if NODE:
        proc = subprocess.run(
            [NODE, "scripts/build-guide.mjs"],
            cwd=WEB,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr


def test_articles_are_scheduled_one_per_day():
    """The drip is the point — two posts on one future day wastes a day."""
    future = sorted(str(a["date"]) for a in DATA if str(a["date"]) > "2026-08-04")
    assert future, "no scheduled articles queued"
    assert len(future) == len(set(future)), f"two articles share a publish date: {future}"


def test_gate_rule_is_duplicated_faithfully():
    """build-guide.mjs is the real gate; catalog.ts must not drift from it."""
    build = BUILD_GUIDE.read_text(encoding="utf-8")
    catalog = CATALOG.read_text(encoding="utf-8")
    for src, name in ((build, "build-guide.mjs"), (catalog, "catalog.ts")):
        assert 'status !== "draft"' in src, f"{name}: draft check missing"
        assert re.search(r'\.date\s*\|\|\s*""\)\s*<=\s*today', src), f"{name}: date gate missing"
        assert "9 * 3600 * 1000" in src, f"{name}: not using the KST day boundary"


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_future_article_is_absent_then_appears():
    """The whole feature, end to end, on the real build script."""
    scheduled = sorted(
        (str(a["date"]), a["slug"]) for a in DATA if str(a["date"]) > "2026-08-04"
    )
    date, slug = scheduled[0]
    day_before = (
        __import__("datetime").date.fromisoformat(date)
        - __import__("datetime").timedelta(days=1)
    ).isoformat()

    out, sitemap = _run_build(day_before)
    assert slug not in sitemap, f"{slug} is in the sitemap a day early"
    assert not (WEB / "public" / "guide" / slug).exists(), f"{slug} HTML exists a day early"
    assert "scheduled" in out

    out, sitemap = _run_build(date)
    assert slug in sitemap, f"{slug} missing from sitemap on its own date"
    assert (WEB / "public" / "guide" / slug / "index.html").exists()


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_output_is_pruned_when_a_date_moves_forward():
    """A slug generated once must not keep serving after it is withheld again."""
    scheduled = sorted(
        (str(a["date"]), a["slug"]) for a in DATA if str(a["date"]) > "2026-08-04"
    )
    date, slug = scheduled[0]
    _run_build(date)
    assert (WEB / "public" / "guide" / slug).exists()

    day_before = (
        __import__("datetime").date.fromisoformat(date)
        - __import__("datetime").timedelta(days=1)
    ).isoformat()
    out, sitemap = _run_build(day_before)
    assert not (WEB / "public" / "guide" / slug).exists(), "stale output survived the gate"
    assert slug not in sitemap
    assert "pruned" in out


def test_daily_workflow_exists_and_targets_the_deploy_branch():
    """A build-time gate does nothing without something rebuilding nightly."""
    wf = ROOT / ".github" / "workflows" / "daily-publish.yml"
    assert wf.exists(), "scheduled articles would never publish themselves"
    text = wf.read_text(encoding="utf-8")
    assert "cron:" in text
    assert "PUBLISH_BRANCH" in text, "a scheduled run checks out the default branch by default"
    assert "submit_indexnow.py" in text, "new URLs should be announced when they go live"
