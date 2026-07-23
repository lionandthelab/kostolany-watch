#!/usr/bin/env python3
"""beforeShellExecution: ask before destructive git / force-push patterns."""

from __future__ import annotations

import json
import re
import sys

DANGEROUS = re.compile(
    r"(git\s+push\s+[^\n]*--force|git\s+push\s+[^\n]*-f\b|git\s+reset\s+--hard|git\s+clean\s+-fd|"
    r"Remove-Item\s+-Recurse\s+-Force\s+\.git)",
    re.IGNORECASE,
)


def main() -> None:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print(json.dumps({"permission": "allow"}))
        return

    cmd = str(payload.get("command") or payload.get("command_line") or "")
    if DANGEROUS.search(cmd):
        print(
            json.dumps(
                {
                    "permission": "ask",
                    "user_message": "Potentially destructive git/shell command — confirm before continuing.",
                    "agent_message": "Hook gated a destructive command. Prefer safe alternatives unless the user explicitly requested it.",
                }
            )
        )
        return

    print(json.dumps({"permission": "allow"}))


if __name__ == "__main__":
    main()
