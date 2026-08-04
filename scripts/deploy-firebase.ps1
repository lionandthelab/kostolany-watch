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

# Load root .env into process (FRED / Resend / cron secret) without printing values
if (Test-Path ".env") {
  Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
    $k, $v = $_.Split('=', 2)
    $k = $k.Trim(); $v = $v.Trim().Trim('"').Trim("'")
    if ($k -and [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($k))) {
      [Environment]::SetEnvironmentVariable($k, $v, "Process")
    }
  }
}

$envArgs = @(
  "DATA_START=2015-01-01",
  "GCS_CACHE_BUCKET=kostolany-watch-cache",
  "NEWSLETTER_SITE_URL=https://kostolany-watch.web.app"
)
if ($env:FRED_API_KEY) { $envArgs += "FRED_API_KEY=$($env:FRED_API_KEY)" }
if ($env:NEWSLETTER_CRON_SECRET) { $envArgs += "NEWSLETTER_CRON_SECRET=$($env:NEWSLETTER_CRON_SECRET)" }
if ($env:VAPID_PUBLIC_KEY) { $envArgs += "VAPID_PUBLIC_KEY=$($env:VAPID_PUBLIC_KEY)" }
if ($env:VAPID_PRIVATE_KEY) { $envArgs += "VAPID_PRIVATE_KEY=$($env:VAPID_PRIVATE_KEY)" }
if ($env:VAPID_MAILTO) { $envArgs += "VAPID_MAILTO=$($env:VAPID_MAILTO)" }
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

if ($env:NEWSLETTER_CRON_SECRET -and $env:VAPID_PUBLIC_KEY -and $env:VAPID_PRIVATE_KEY) {
  Write-Host "== Push Cloud Scheduler ==" -ForegroundColor Cyan
  & "$PSScriptRoot\setup-push-scheduler.ps1" -ProjectId $ProjectId -Region $Region -Service $Service
}

# Tell Bing/Yandex what changed. The script reads web/public/sitemap.xml, which
# the build above just regenerated, so new guide articles are included without
# anyone remembering to add them. Never fatal — a failed notification must not
# fail a successful deploy.
Write-Host "== Notify IndexNow ==" -ForegroundColor Cyan
$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" }
      elseif (Test-Path ".venv/bin/python") { ".venv/bin/python" }
      else { "python" }
try {
  & $py scripts/submit_indexnow.py
  if ($LASTEXITCODE -ne 0) { Write-Host "  IndexNow returned $LASTEXITCODE (deploy unaffected)" -ForegroundColor Yellow }
} catch {
  Write-Host "  IndexNow skipped: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "Done." -ForegroundColor Green
Write-Host "Hosting: https://$ProjectId.web.app"
Write-Host "API health: https://$ProjectId.web.app/api/health"
