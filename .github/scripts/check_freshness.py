#!/usr/bin/env python3
"""Poll the live freshness endpoint and turn its answer into an exit code.

Lives under .github/ rather than scripts/ because it is workflow plumbing —
nothing in src/kostolany imports it and it takes no repo dependencies (stdlib
only, so the watchdog needs no install step that could itself flake).

Contract assumed of GET /api/health/freshness (owned by another lane):
  200 {"ok": bool, "breaches": [str, ...]}

Why the exit code is split five ways: a red run has to say *why* it is red.
"the endpoint 404s because it has not shipped yet" and "the endpoint shipped
and is telling us the served data is stale" are the same colour in the Actions
UI, and only one of them is an incident. So each failure mode gets its own code
and its own banner line in the log.

Exit codes:
  0  OK            ok=true
  1  STALE         ok=false, a real freshness breach; breaches are listed
  2  NOT_DEPLOYED  404 on every attempt; route not shipped yet
  3  UNREACHABLE   transport error / 5xx / unexpected status on every attempt
  4  BAD_RESPONSE  200 but the body is not the agreed contract
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

OK = 0
STALE = 1
NOT_DEPLOYED = 2
UNREACHABLE = 3
BAD_RESPONSE = 4

NAMES = {
    OK: "OK",
    STALE: "STALE",
    NOT_DEPLOYED: "NOT_DEPLOYED",
    UNREACHABLE: "UNREACHABLE",
    BAD_RESPONSE: "BAD_RESPONSE",
}

DEFAULT_URL = "https://kostolany-watch.web.app/api/health/freshness"


def fetch(url: str, timeout: float) -> tuple[int | None, str, str | None]:
    """Return (http_status, body, transport_error). Status is None on transport error."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "kostolany-freshness-watch",
            "Accept": "application/json",
            # Hosting caches /api/** only if the origin says so, but ask anyway:
            # a watchdog reading a cached body would be reporting the past.
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), None
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        return exc.code, body, None
    except Exception as exc:  # URLError, socket.timeout, TLS, DNS
        return None, "", f"{type(exc).__name__}: {exc}"


def retryable(status: int | None, transport_error: str | None) -> bool:
    """Retry anything that a redeploy or a cold start could plausibly cause.

    404 is on this list deliberately. The API runs minScale=1, but during a
    Cloud Run rollout the old revision can answer for a few seconds and it does
    not know the route — retrying is what stops a routine redeploy from paging
    us. A route that genuinely does not exist stays 404 through every attempt.
    """
    if transport_error is not None:
        return True
    return status == 404 or status == 408 or status == 429 or (status is not None and status >= 500)


def classify(status: int | None, body: str, transport_error: str | None) -> tuple[int, str, list[str]]:
    """Return (exit_code, headline, detail_lines)."""
    if transport_error is not None:
        return UNREACHABLE, f"endpoint unreachable ({transport_error})", []
    if status == 404:
        return (
            NOT_DEPLOYED,
            "endpoint returned 404 - /api/health/freshness is not deployed yet",
            [
                "The API answered, so Cloud Run and the Hosting rewrite are fine;",
                "the route itself is missing. This is a pending deploy, not a data incident.",
                f"body: {body.strip()[:300]}",
            ],
        )
    if status != 200:
        return (
            UNREACHABLE,
            f"endpoint returned HTTP {status}",
            [f"body: {body.strip()[:300]}"],
        )

    try:
        payload = json.loads(body)
    except Exception as exc:
        return BAD_RESPONSE, f"200 but body is not JSON ({exc})", [f"body: {body.strip()[:300]}"]
    if not isinstance(payload, dict) or "ok" not in payload:
        return (
            BAD_RESPONSE,
            "200 but body has no top-level `ok` - contract broken",
            [f"body: {body.strip()[:300]}"],
        )

    breaches = payload.get("breaches") or []
    if not isinstance(breaches, list):
        breaches = [str(breaches)]
    breaches = [str(b) for b in breaches]

    if payload["ok"] is True:
        return OK, f"ok=true, {len(breaches)} breach(es) reported", breaches
    # Anything other than literal true is a breach. A missing/!=true `ok` with an
    # empty `breaches` list still fails: the endpoint said not-ok and that is the
    # signal, whether or not it managed to name a reason.
    return STALE, f"ok={payload['ok']!r} - served data is stale", breaches


def _ascii(text: str) -> str:
    """Strip anything a legacy console codepage cannot render.

    Not cosmetic. Two sources of non-ASCII leak in from outside: OS-localised
    socket error strings (a Korean Windows box says the refusal in Hangul) and
    whatever the endpoint puts in `breaches`. Printing those raw makes a
    maintainer's console throw UnicodeDecodeError *instead of* showing the
    banner that says what is wrong, which is the one moment it must not.
    """
    return text.encode("ascii", "replace").decode("ascii")


def emit(name: str, headline: str, detail: list[str], attempts_used: int, url: str) -> None:
    headline = _ascii(headline)
    detail = [_ascii(d) for d in detail]
    tag = "notice" if name == "OK" else "error"
    print(f"::{tag} title=freshness {name}::{headline}")
    print(f"FRESHNESS: {name} - {headline}")
    print(f"  url      : {url}")
    print(f"  attempts : {attempts_used}")
    for line in detail:
        print(f"  {line}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        icon = "OK" if name == "OK" else "FAIL"
        lines = [
            f"### Freshness watch: {icon} `{name}`",
            "",
            headline,
            "",
            f"- endpoint: `{url}`",
            f"- attempts: {attempts_used}",
        ]
        if detail:
            lines += ["", "```", *detail, "```"]
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"status={name}\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--attempts", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=45.0)
    ap.add_argument("--retry-delay", type=float, default=10.0)
    args = ap.parse_args(argv)

    status = body = transport_error = None
    used = 0
    for attempt in range(1, max(1, args.attempts) + 1):
        used = attempt
        status, body, transport_error = fetch(args.url, args.timeout)
        where = _ascii(transport_error or f"HTTP {status}")
        print(f"attempt {attempt}/{args.attempts}: {where}")
        if not retryable(status, transport_error):
            break
        if attempt < args.attempts:
            # Linear backoff, not exponential: the failure we are riding out is a
            # cold start or a rollout, both of which finish in tens of seconds.
            delay = args.retry_delay * attempt
            print(f"  retryable - sleeping {delay:.0f}s")
            time.sleep(delay)

    code, headline, detail = classify(status, body or "", transport_error)
    emit(NAMES[code], headline, detail, used, args.url)
    return code


if __name__ == "__main__":
    sys.exit(main())
