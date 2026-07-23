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
# From repo root
.\scripts\deploy-firebase.ps1
```

Or manually:

```powershell
cd web; npm ci; npm run build; cd ..
gcloud run deploy kostolany-api --source . --region asia-northeast3 --allow-unauthenticated --memory 2Gi --cpu 1 --timeout 300 --max-instances 3 --min-instances 0 --set-env-vars DATA_START=2015-01-01
firebase deploy --only hosting
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
