#!/usr/bin/env python3
"""stop hook: if recent edits touched ML core, remind/follow up with verify."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKER = ROOT / ".cursor" / "hooks" / ".needs_verify"


def main() -> None:
    raw = sys.stdin.read()
    try:
        _payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        _payload = {}

    # If a previous afterFileEdit flagged ML files, request verify follow-up
    if MARKER.exists():
        try:
            MARKER.unlink()
        except OSError:
            pass
        out = {
            "followup_message": (
                "ML/harness files were edited this session. "
                "Run skill kostolany-verify: `python scripts/agent_verify.py` "
                "and fix failures before claiming done."
            )
        }
        print(json.dumps(out))
        return

    print("{}")


if __name__ == "__main__":
    main()
