# Deploy Kostolany Watch → Cloud Run API + Firebase Hosting
param(
  [string]$ProjectId = "",
  [string]$Region = "asia-northeast3",
  [string]$Service = "kostolany-api",
  [string]$SaKey = ".secrets/firebase-deploy.json"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not $ProjectId) {
  if (Test-Path ".firebaserc") {
    $fb = Get-Content ".firebaserc" -Raw | ConvertFrom-Json
    $ProjectId = $fb.projects.default
  }
}
if (-not $ProjectId) { throw "Set project id in .firebaserc or pass -ProjectId" }

Write-Host "== Project: $ProjectId  Region: $Region ==" -ForegroundColor Cyan

gcloud config set project $ProjectId | Out-Null

Write-Host "== Build web ==" -ForegroundColor Cyan
Push-Location web
if (Test-Path package-lock.json) { npm ci } else { npm install }
npm run build
Pop-Location

Write-Host "== Deploy Cloud Run API ==" -ForegroundColor Cyan
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com firebasehosting.googleapis.com --quiet

$envArgs = @("DATA_START=2015-01-01", "GCS_CACHE_BUCKET=kostolany-watch-cache")
if ($env:FRED_API_KEY) { $envArgs += "FRED_API_KEY=$($env:FRED_API_KEY)" }
$envJoined = ($envArgs -join ",")

# Ensure durable cache bucket exists (idempotent; ignore already-exists noise)
$ErrorActionPreference = "Continue"
gsutil ls "gs://kostolany-watch-cache" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
  gsutil mb -p $ProjectId -l $Region "gs://kostolany-watch-cache" 2>$null | Out-Null
}
$projectNumber = (gcloud projects describe $ProjectId --format="value(projectNumber)")
$runSa = "$projectNumber-compute@developer.gserviceaccount.com"
gsutil iam ch "serviceAccount:${runSa}:roles/storage.objectAdmin" "gs://kostolany-watch-cache" 2>$null | Out-Null
$ErrorActionPreference = "Stop"

gcloud run deploy $Service `
  --source . `
  --region $Region `
  --allow-unauthenticated `
  --cpu 2 `
  --memory 4Gi `
  --timeout 300 `
  --concurrency 80 `
  --max-instances 5 `
  --min-instances 1 `
  --no-cpu-throttling `
  --set-env-vars $envJoined `
  --project $ProjectId

Write-Host "== Deploy Firebase Hosting ==" -ForegroundColor Cyan
if (Test-Path $SaKey) {
  $env:GOOGLE_APPLICATION_CREDENTIALS = (Resolve-Path $SaKey).Path
  Write-Host "Using service account key: $SaKey"
} else {
  Write-Host "No $SaKey — using firebase login credentials (run firebase login if needed)"
}

firebase deploy --only hosting --project $ProjectId --non-interactive

Write-Host "Done." -ForegroundColor Green
Write-Host "Hosting: https://$ProjectId.web.app"
Write-Host "API health: https://$ProjectId.web.app/api/health"
