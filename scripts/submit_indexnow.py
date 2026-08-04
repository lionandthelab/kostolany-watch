#!/usr/bin/env python3
"""Submit key URLs to IndexNow (Bing / Yandex / compatible engines)."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

SITE = "https://kostolany-watch.web.app"
DEFAULT_URLS = [
    f"{SITE}/",
    f"{SITE}/watch",
    f"{SITE}/macro",
    f"{SITE}/news",
    f"{SITE}/about",
    f"{SITE}/guide/",
    f"{SITE}/guide/kostolany-egg/",
    f"{SITE}/guide/six-regimes/",
]


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
    parser.add_argument("urls", nargs="*", default=DEFAULT_URLS)
    args = parser.parse_args()
    key = resolve_key(args.key)
    payload = {
        "host": "kostolany-watch.web.app",
        "key": key,
        "keyLocation": f"{SITE}/{key}.txt",
        "urlList": args.urls,
    }
    req = urllib.request.Request(
        "https://api.indexnow.org/indexnow",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(json.dumps({"status": resp.status, "body": body, "urls": len(args.urls)}, indent=2))
    except urllib.error.HTTPError as exc:
        print(json.dumps({"status": exc.code, "body": exc.read().decode("utf-8", errors="replace")}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
