#!/usr/bin/env python3
"""afterFileEdit: flag leakage-sensitive paths so stop hook can demand verify."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKER = ROOT / ".cursor" / "hooks" / ".needs_verify"

WATCH = (
    "src/kostolany/harness/",
    "src/kostolany/labels.py",
    "src/kostolany/models.py",
    "src/kostolany/engine.py",
    "tests/test_harness.py",
)


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("{}")
        return

    # Common field names across Cursor hook versions
    path = (
        payload.get("file_path")
        or payload.get("path")
        or payload.get("file")
        or ""
    )
    path = str(path).replace("\\", "/")
    rel = path
    if "kostolany-watch/" in path:
        rel = path.split("kostolany-watch/", 1)[-1]

    matched = any(w in rel for w in WATCH)
    if not matched:
        print("{}")
        return

    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(rel, encoding="utf-8")
    print(
        json.dumps(
            {
                "additional_context": (
                    f"Edited leakage-sensitive file `{rel}`. "
                    "Remember: gold/planted labels are eval-only; "
                    "finish with `python scripts/agent_verify.py`."
                )
            }
        )
    )


if __name__ == "__main__":
    main()
