---
name: kostolany-deploy
description: Deploy Kostolany Watch (Cloud Run API, Firebase Hosting, IndexNow notification) on any OS. Use when asked to deploy, ship, release, publish, or push this site live, or when a change needs to reach kostolany-watch.web.app. Covers the POSIX path that scripts/deploy-firebase.ps1 cannot run.
---

# Kostolany Watch — deploy

`scripts/deploy-firebase.ps1` is the Windows path. This skill is the same
procedure written so it runs anywhere, because PowerShell is not guaranteed on
Linux/macOS. Prefer the `.ps1` when on Windows; follow the steps below
otherwise, or when a partial deploy is wanted.

## 0. Pick the scope first — this matters

The deploy script ships the **working tree**, not the last commit.

| Changed | Deploy |
|---|---|
| Python only (`src/kostolany/**`) | Cloud Run only — skip Hosting |
| Frontend (`web/src/**`, articles, styles) | Both |
| Guide articles only | Hosting only |

Shipping Hosting drags any uncommitted frontend work live with it. If the tree
has unrelated in-flight changes, say so and confirm scope before deploying.

## 1. Resolve the interpreter (OS-independent)

```bash
PY=".venv/bin/python"; [ -x "$PY" ] || PY=".venv/Scripts/python.exe"; [ -x "$PY" ] || PY="python3"
echo "$PY"
```

## 2. Preflight — never deploy red

```bash
"$PY" -m pytest tests/ -q
"$PY" scripts/agent_verify.py       # must print: agent_verify: PASS
"$PY" -m ruff check src/ tests/
(cd web && npm run build)            # prebuild: guide+sitemap, postbuild: route shells
```

`agent_verify` is the repo's merge gate (`AGENTS.md` non-negotiable 4). A
failing gate is never worked around — fix the cause.

The web build has two hooks that must both run, so use `npm run build`, never
`vite build` directly:
- `prebuild` → `build-guide.mjs` regenerates guide HTML, `sitemap.xml`, `feed.xml`
- `postbuild` → `prerender-routes.mjs` writes `dist/{watch,macro,news,about}.html`

## 3. Deploy Cloud Run

Omit `--set-env-vars` so existing secrets on the service are preserved.

```bash
gcloud run deploy kostolany-api \
  --source . --region asia-northeast3 --project kostolany-watch \
  --allow-unauthenticated --cpu 2 --memory 4Gi --timeout 300 \
  --concurrency 80 --max-instances 5 --min-instances 1 --no-cpu-throttling
```

## 4. Deploy Hosting (only if the frontend changed)

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/.secrets/firebase-deploy.json"
firebase deploy --only hosting --project kostolany-watch --non-interactive
```

Without the service-account key, `firebase login` credentials are used; in a
non-interactive shell that key is usually the only thing that works.

## 5. Notify IndexNow — always, after Hosting

```bash
"$PY" scripts/submit_indexnow.py            # add --dry-run to inspect first
```

Reads `web/public/sitemap.xml`, so newly published guide articles are included
automatically. `status: 200` means accepted; 403/422 means the key and its
published location disagree. Google ignores IndexNow — see step 7.

Never let a failed notification fail the deploy.

## 6. Verify live

```bash
curl -s -o /dev/null -w "api %{http_code}\n" https://kostolany-watch.web.app/api/health

# Per-route shells: distinct titles, 200, no redirect hop
for r in "" watch macro news about; do
  printf "/%-7s [%s] " "$r" "$(curl -s -o /dev/null -w '%{http_code}' https://kostolany-watch.web.app/$r)"
  curl -s "https://kostolany-watch.web.app/$r" | grep -o '<title>[^<]*</title>' | head -1
done
```

A `301` on `/watch` means the prerender emitted a directory instead of
`watch.html` — check the rewrites in `firebase.json`.

## 7. What this cannot do

Google does not accept IndexNow. Sitemap submission and index requests happen
in Google Search Console by hand, under `색인 생성 > Sitemaps` and the URL
inspection tool. Report that as a remaining manual step rather than implying
the deploy covered it.

## Notes

- Cloud Scheduler jobs (`push-daily-dispatch`, `ledger-daily-record`) are set up
  once by their own scripts, not per deploy.
- The daily ledger keeps recording regardless of deploys; a deploy never
  rewrites an archived day (`docs/LEDGER.md`).
- The repo's generic `/deploy` skill targets Flutter/pubspec projects and does
  not apply here.
