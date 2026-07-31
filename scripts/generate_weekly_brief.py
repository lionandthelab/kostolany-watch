#!/usr/bin/env python3
"""Generate a weekly regime brief on THIS machine via Claude Code CLI, publish + email.

Requires:
  - Claude Code CLI logged in (`claude` on PATH, or npx @anthropic-ai/claude-code)
  - NEWSLETTER_CRON_SECRET in repo .env (same as Cloud Run)
  - PC awake at scheduled time (local Task Scheduler — not Cloud)

Usage:
  python scripts/generate_weekly_brief.py
  python scripts/generate_weekly_brief.py --dry-run
  python scripts/generate_weekly_brief.py --no-dispatch
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SITE = os.environ.get("NEWSLETTER_SITE_URL") or "https://kostolany-watch.web.app"
API = os.environ.get("KOSTOLANY_API_BASE") or f"{SITE}/api"


def load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def find_claude() -> list[str]:
    for cand in ("claude", "claude.exe"):
        p = shutil.which(cand)
        if p:
            return [p]
    npx = shutil.which("npx")
    if npx:
        return [npx, "--yes", "@anthropic-ai/claude-code"]
    raise SystemExit(
        "Claude Code CLI not found. Install: npm i -g @anthropic-ai/claude-code\n"
        "Then run `claude` once to log in with your Claude subscription."
    )


def fetch_context(client: httpx.Client) -> dict:
    ctx: dict = {"asof": datetime.now(timezone.utc).isoformat(), "markets": [], "news": {}}
    for sym, label in (("^GSPC", "US S&P 500"), ("BTC-USD", "Bitcoin")):
        try:
            # Prefer lightweight snapshot
            r = client.get(f"{API}/snapshot", params={"symbol": sym, "model": "momo"}, timeout=60.0)
            if r.status_code == 200:
                snap = r.json()
                ctx["markets"].append(
                    {
                        "symbol": sym,
                        "label": label,
                        "regime": snap.get("regime"),
                        "regime_name_ko": snap.get("regime_name_ko"),
                        "confidence": snap.get("confidence"),
                        "gauges": snap.get("gauges"),
                        "vote": snap.get("vote"),
                        "action_ko": snap.get("action_ko"),
                    }
                )
                continue
        except Exception as exc:  # noqa: BLE001
            ctx.setdefault("errors", []).append(f"snapshot {sym}: {exc}")
        ctx["markets"].append({"symbol": sym, "label": label, "error": "unavailable"})

    try:
        nr = client.get(f"{API}/news", timeout=60.0)
        if nr.status_code == 200:
            desk = nr.json()
            ctx["news"] = {
                "priority_summary_md": desk.get("priority_summary_md"),
                "items": [
                    {
                        "title": it.get("title"),
                        "theme": it.get("theme"),
                        "source": it.get("source"),
                    }
                    for it in (desk.get("items") or [])[:12]
                ],
            }
    except Exception as exc:  # noqa: BLE001
        ctx.setdefault("errors", []).append(f"news: {exc}")
    return ctx


def build_prompt(ctx: dict, week_date: str, n: int) -> str:
    from kostolany.brief_style import compact_context_for_llm

    slim = compact_context_for_llm(ctx)
    return f"""You write a weekly market BRIEFING CARD for Kostolany Watch readers.
Audience: busy adults who read Fed/Bloomberg-style morning notes.
Language: Korean + English. Educational regime lens — NOT investment advice, no buy/sell tips.

Return ONLY valid JSON (no markdown fences, no ## headings anywhere):
{{
  "slug": "weekly-{week_date}",
  "kind": "weekly",
  "date": "{week_date}",
  "title": {{"ko": "...", "en": "..."}},
  "description": {{"ko": "one-line dek", "en": "one-line dek"}},
  "body": {{"ko": "HTML only", "en": "HTML only"}}
}}

TITLE style (news card, not academic):
- KO: "주간 브리핑 #{n} · <구체적 한 줄 훅>"  (date optional in title)
- EN: "Weekly brief #{n}: <concrete hook>"
- Hook must name a real tension (e.g. rates held vs credit stress), not "확신은 낮음".

BODY HTML structure — use ONLY these tags: <p> <h2> <ul> <ol> <li> <a> <strong> <em>
Exactly this outline for KO (mirror in EN):

1) <p class="lead">…</p>  — 2–3 sentences. What the week’s picture is. Plain language.
2) <h2>한눈에</h2>
   <ul>
     <li><strong>미국</strong> — regime code + short plain label (e.g. B3 과장·하락). Optional agreement words like "의견 갈림" — NEVER confidence %, NEVER 0.25, NEVER "25%".</li>
     <li><strong>비트코인</strong> — same style</li>
     <li><strong>게이지</strong> — one qualitative line (e.g. 유동성은 버티는데 심리는 식은 편)</li>
   </ul>
3) <h2>왜 중요한가</h2>
   <p>…</p> Connect regimes to 1–2 real headline themes (FOMC, credit, crypto policy). Explain the tension. No model navel-gazing.
4) <h2>이번 주 체크포인트</h2>
   <ol>
     <li>Concrete observation (link <a href="/watch">국면</a> only if useful)</li>
     <li>Concrete observation (macro/news)</li>
     <li>Concrete observation</li>
   </ol>
5) <h2>헤드라인 3</h2>
   <ul>
     <li><strong>short headline</strong> — one sentence why it matters for regime reading (use CONTEXT headlines; you may paraphrase titles, do not invent fake URLs — omit href if unsure)</li>
     … exactly 3 items
   </ul>
6) <p class="kicker">한 줄</p><p>…memorable non-percentage takeaway…</p>
7) <p class="disclaimer">본 정보는 교육·연구 목적의 국면 인식 보조 자료이며 투자 권유·자문이 아닙니다.</p>

HARD BANS:
- No markdown (# ## **, fences)
- No confidence / 확신도 / 25% / 0.25 / "mixed tier" as the story
- No "습관 만들기" meta essays
- No inventing prices or fake data
- Keep each language under 350 words
- Prefer clarity over hedging

CONTEXT (already cleaned — do not reintroduce confidence numbers):
{json.dumps(slim, ensure_ascii=False, indent=2)}
"""


def write_static_guide_page(brief: dict) -> Path:
    """Write crawlable HTML so /guide/<slug>/ is not an outdated static leftover."""
    from html import escape

    slug = brief["slug"]
    title = escape(str(brief["title"]["ko"]))
    desc = escape(str((brief.get("description") or {}).get("ko") or title))
    body = brief["body"]["ko"]
    out_dir = ROOT / "web" / "public" / "guide" / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    html = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} · Kostolany Watch</title>
  <meta name="description" content="{desc}" />
  <link rel="canonical" href="{SITE}/guide/{slug}/" />
  <style>
    :root {{ --ink:#1a1f1c; --muted:#5c6b63; --moss:#2f5d50; --line:#d5ddd6; }}
    body {{ margin:0; font-family:"IBM Plex Sans",system-ui,sans-serif; color:var(--ink);
      background:linear-gradient(165deg,#f7f3ea,#e7efe4 55%,#dfe8df); line-height:1.65; }}
    .wrap {{ width:min(42rem, calc(100% - 2rem)); margin:0 auto; padding:2rem 0 4rem; }}
    a {{ color:var(--moss); }}
    h1 {{ font-family:Fraunces,Georgia,serif; font-size:clamp(1.5rem,4vw,2rem); color:var(--moss); line-height:1.25; margin:0; }}
    h2 {{ font-family:Fraunces,Georgia,serif; font-size:1.15rem; margin:1.6rem 0 0.45rem; color:var(--moss); }}
    .meta {{ color:var(--muted); font-size:0.9rem; margin:0.45rem 0 1.2rem; }}
    .nav {{ display:flex; gap:1rem; flex-wrap:wrap; margin-bottom:1.25rem; font-size:0.92rem; }}
    article {{ background:rgba(255,255,255,0.5); border:1px solid var(--line); border-radius:14px; padding:1.25rem 1.35rem 1.5rem; }}
    .lead {{ font-size:1.05rem; }}
    .kicker {{ color:var(--muted); font-size:0.85rem; margin-bottom:0.15rem; }}
    .disclaimer {{ color:var(--muted); font-size:0.88rem; border-top:1px solid var(--line); padding-top:1rem; margin-top:1.5rem; }}
    ul,ol {{ padding-left:1.2rem; }}
    li {{ margin:0.35rem 0; }}
  </style>
</head>
<body>
  <div class="wrap">
    <nav class="nav">
      <a href="/">Kostolany Watch</a>
      <a href="/guide">Guide</a>
      <a href="/watch">Regime</a>
      <a href="/news">News</a>
    </nav>
    <article>
      <h1>{title}</h1>
      <p class="meta">업데이트 {escape(str(brief.get("date") or ""))}</p>
      {body}
    </article>
  </div>
</body>
</html>
"""
    path = out_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


def run_claude(prompt: str) -> str:
    """Pass prompt on stdin — Windows CreateProcess argv length limit otherwise."""
    cmd = find_claude() + [
        "-p",
        "--output-format",
        "text",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"claude failed ({proc.returncode}): {proc.stderr[-2000:]}\n{proc.stdout[-2000:]}"
        )
    return proc.stdout.strip()


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise
        return json.loads(m.group(0))


def main() -> None:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD for slug")
    ap.add_argument("--dry-run", action="store_true", help="Print JSON only; do not POST")
    ap.add_argument("--no-dispatch", action="store_true", help="Save brief but skip email")
    ap.add_argument("--number", type=int, default=0, help="Brief number override")
    ap.add_argument(
        "--deploy-hosting",
        action="store_true",
        help="After publish, rebuild web and firebase deploy hosting",
    )
    args = ap.parse_args()

    secret = (os.environ.get("NEWSLETTER_CRON_SECRET") or "").strip()
    if not args.dry_run and not secret:
        raise SystemExit("NEWSLETTER_CRON_SECRET missing in .env")

    with httpx.Client(timeout=90.0, follow_redirects=True) as client:
        ctx = fetch_context(client)
        # estimate number from existing index
        n = args.number
        if n <= 0:
            try:
                idx = client.get(f"{API}/briefs", params={"kind": "weekly", "limit": 50})
                n = len(idx.json().get("items") or []) + 1 if idx.status_code == 200 else 1
            except Exception:  # noqa: BLE001
                n = 1

        prompt = build_prompt(ctx, args.date, n)
        print("Calling Claude Code CLI…", file=sys.stderr)
        raw = run_claude(prompt)
        brief = extract_json(raw)
        brief["slug"] = f"weekly-{args.date}"
        brief["kind"] = "weekly"
        brief["date"] = args.date
        brief["source"] = "claude-local"
        from kostolany.brief_style import sanitize_brief_html

        body = brief.get("body") or {}
        brief["body"] = {
            "ko": sanitize_brief_html(str(body.get("ko") or "")),
            "en": sanitize_brief_html(str(body.get("en") or "")),
        }
        brief["dispatch"] = not args.no_dispatch and not args.dry_run

        out_path = ROOT / "artifacts" / "logs" / f"weekly-{args.date}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"slug": brief["slug"], "title": brief.get("title")}, ensure_ascii=False))
        if args.dry_run:
            print(f"dry-run wrote {out_path}", file=sys.stderr)
            return

        res = client.post(
            f"{API}/briefs",
            headers={
                "Content-Type": "application/json",
                "X-Cron-Secret": secret,
            },
            json=brief,
        )
        print(f"POST /briefs → {res.status_code}", file=sys.stderr)
        print(res.text[:1500])
        if res.status_code >= 400:
            raise SystemExit(1)

        static_path = write_static_guide_page(brief)
        print(f"static → {static_path}", file=sys.stderr)
        if not args.deploy_hosting:
            print("skip hosting (pass --deploy-hosting to refresh Firebase)", file=sys.stderr)
            return
        try:
            subprocess.run(
                ["npm", "run", "build"],
                cwd=str(ROOT / "web"),
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            write_static_guide_page(brief)
            dist = ROOT / "web" / "dist" / "guide" / brief["slug"]
            dist.mkdir(parents=True, exist_ok=True)
            (dist / "index.html").write_text(
                static_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
            subprocess.run(
                [
                    "firebase",
                    "deploy",
                    "--only",
                    "hosting",
                    "--project",
                    "kostolany-watch",
                    "--non-interactive",
                ],
                cwd=str(ROOT),
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            print("hosting deploy attempted", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"hosting refresh skipped: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
