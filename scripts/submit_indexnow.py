#!/usr/bin/env python3
"""Submit key URLs to IndexNow (Bing / Yandex / compatible engines)."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

SITE = "https://kostolany-watch.web.app"


def sitemap_urls() -> list[str]:
    """Read the generated sitemap so new guide articles are never missed.

    This list used to be hardcoded, which meant every article published after
    it was written silently went unsubmitted. `build-guide.mjs` regenerates
    web/public/sitemap.xml from articles.json on every build, so reading it
    keeps submission in sync by construction.
    """
    root = Path(__file__).resolve().parents[1]
    sitemap = root / "web" / "public" / "sitemap.xml"
    if not sitemap.exists():
        raise SystemExit("sitemap.xml missing — run `npm run build` in web/ first")
    urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sitemap.read_text(encoding="utf-8"))
    if not urls:
        raise SystemExit("sitemap.xml has no <loc> entries")
    return urls


def resolve_key(explicit: str | None) -> str:
    if explicit:
        return explicit.strip()
    root = Path(__file__).resolve().parents[1]
    well_known = root / "web" / "public" / ".well-known" / "indexnow-key.txt"
    if well_known.exists():
        return well_known.read_text(encoding="utf-8").strip()
    for path in (root / "web" / "public").glob("*.txt"):
        text = path.read_text(encoding="utf-8").strip()
        if text and path.stem == text:
            return text
    raise SystemExit("IndexNow key not found under web/public/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default=None)
    parser.add_argument("--dry-run", action="store_true", help="print the payload, submit nothing")
    parser.add_argument("urls", nargs="*")
    args = parser.parse_args()
    urls = args.urls or sitemap_urls()
    key = resolve_key(args.key)
    payload = {
        "host": "kostolany-watch.web.app",
        "key": key,
        "keyLocation": f"{SITE}/{key}.txt",
        "urlList": urls,
    }
    if args.dry_run:
        print(json.dumps({"would_submit": len(urls), "urls": urls}, indent=2))
        return 0
    req = urllib.request.Request(
        "https://api.indexnow.org/indexnow",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(json.dumps({"status": resp.status, "body": body, "urls": len(urls)}, indent=2))
    except urllib.error.HTTPError as exc:
        print(json.dumps({"status": exc.code, "body": exc.read().decode("utf-8", errors="replace")}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
