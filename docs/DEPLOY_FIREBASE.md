# Firebase + Cloud Run deploy

## Architecture

```
Browser → Firebase Hosting (web/dist)
            ├─ static assets
            └─ /api/**  →  Cloud Run service `kostolany-api` (FastAPI)
```

Region default: `asia-northeast3` (Seoul)

## Prerequisites

1. `gcloud` authenticated as a project owner
2. Billing-enabled GCP/Firebase project (default: `kostolany-watch`)
3. Optional: `.secrets/firebase-deploy.json` service-account key for non-interactive Hosting deploys
   (otherwise `firebase login` in a normal terminal)
4. APIs: Cloud Run, Cloud Build, Artifact Registry, Firebase Hosting

## Live

- Hosting: https://kostolany-watch.web.app
- API: https://kostolany-watch.web.app/api/health
- Cloud Run: `kostolany-api` in `asia-northeast3`

## One-time setup (already done for `kostolany-watch`)

```bash
gcloud config set project kostolany-watch
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firebase.googleapis.com firebasehosting.googleapis.com

# Hosting deploy SA (key lives in .secrets/, gitignored)
# gcloud iam service-accounts create firebase-deploy ...
# Optional: FRED_API_KEY on Cloud Run
# gcloud run services update kostolany-api --region=asia-northeast3 --update-env-vars FRED_API_KEY=...
```

Put the project id in `.firebaserc` if different.

## Deploy

```powershell
# From repo root (Windows)
.\scripts\deploy-firebase.ps1
```

**Not on Windows?** Use the `kostolany-deploy` skill
(`.claude/skills/kostolany-deploy/SKILL.md`) — same procedure, no PowerShell.

The script ships the **working tree**, not the last commit, and deploys Hosting
as well as Cloud Run. If the tree carries unrelated in-flight frontend work,
deploy Cloud Run alone instead of running the whole script.

Steps the script performs, in order:

1. `npm run build` in `web/` — `prebuild` regenerates guide HTML + `sitemap.xml`,
   `postbuild` writes the per-route shells (`dist/watch.html` and friends).
   Always `npm run build`, never `vite build` directly, or both hooks are skipped.
2. `gcloud run deploy kostolany-api`
3. `firebase deploy --only hosting`
4. Push Cloud Scheduler setup (only when the VAPID/cron env vars are present)
5. **IndexNow notification** — `scripts/submit_indexnow.py`, which reads the
   sitemap just regenerated in step 1 so new guide articles are included
   automatically. Non-fatal: a failed notification never fails a deploy.

Google does **not** accept IndexNow. Sitemap submission and index requests are
manual, in Google Search Console.

Or manually:

```powershell
cd web; npm ci; npm run build; cd ..
gcloud run deploy kostolany-api --source . --region asia-northeast3 --allow-unauthenticated --memory 2Gi --cpu 1 --timeout 300 --max-instances 3 --min-instances 0 --set-env-vars DATA_START=2015-01-01
firebase deploy --only hosting
.\.venv\Scripts\python.exe scripts\submit_indexnow.py
```

## Local check after API prefix change

```powershell
kostolany serve
# health: http://127.0.0.1:8000/api/health
cd web; npm run dev
```

## Notes

- First Cloud Run request can be slow (model fit + data download).
- Set `FRED_API_KEY` on Cloud Run for better liquidity/sentiment features.
- Install Korea extras in the image is best-effort (`pykrx` / FinanceDataReader).
