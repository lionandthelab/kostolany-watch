# Enable the daily point-in-time ledger job (Cloud Scheduler -> Cloud Run).
# Runs in the cloud, so the PC being off is irrelevant — see docs/LEDGER.md.
param(
  [string]$ProjectId = "kostolany-watch",
  [string]$Region = "asia-northeast3",
  [string]$Service = "kostolany-api",
  # 23:50 KST = 14:50 UTC — end of the Seoul day, after the 22:00 daily card.
  [string]$Schedule = "50 14 * * *",
  [string]$TimeZone = "Etc/UTC"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (Test-Path ".env") {
  Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
    $k, $v = $_.Split('=', 2)
    $k = $k.Trim(); $v = $v.Trim().Trim('"').Trim("'")
    if ($k -and -not [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($k))) { return }
    [Environment]::SetEnvironmentVariable($k, $v, "Process")
  }
}

if (-not $env:NEWSLETTER_CRON_SECRET) {
  Write-Host "ERROR: NEWSLETTER_CRON_SECRET missing. Run scripts/setup-newsletter-scheduler.ps1 first." -ForegroundColor Red
  exit 1
}

Write-Host "== Enable APIs ==" -ForegroundColor Cyan
gcloud services enable cloudscheduler.googleapis.com run.googleapis.com --project=$ProjectId --quiet

# The ledger is worthless on an ephemeral Cloud Run disk — GCS is the archive.
Write-Host "== Check durable storage ==" -ForegroundColor Cyan
$envNames = gcloud run services describe $Service --region=$Region --project=$ProjectId `
  --format="value(spec.template.spec.containers[0].env[].name)"
if ($envNames -notmatch "GCS_CACHE_BUCKET") {
  Write-Host "ERROR: GCS_CACHE_BUCKET not set on $Service. The ledger would die with the instance." -ForegroundColor Red
  exit 1
}
Write-Host "  OK archive bucket configured"

$name = "ledger-daily-record"
$uri = "https://kostolany-watch.web.app/api/ledger/record"
$headerArg = "Content-Type=application/json,X-Cron-Secret=$($env:NEWSLETTER_CRON_SECRET)"

Write-Host "== Upsert Cloud Scheduler job ==" -ForegroundColor Cyan
$ErrorActionPreference = "Continue"
gcloud scheduler jobs describe $name --location=$Region --project=$ProjectId 2>$null | Out-Null
$exists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"

if ($exists) {
  gcloud scheduler jobs update http $name `
    --location=$Region --project=$ProjectId `
    --schedule=$Schedule --time-zone=$TimeZone `
    --uri=$uri --http-method=POST `
    --update-headers=$headerArg --attempt-deadline=300s | Out-Null
} else {
  gcloud scheduler jobs create http $name `
    --location=$Region --project=$ProjectId `
    --schedule=$Schedule --time-zone=$TimeZone `
    --uri=$uri --http-method=POST `
    --headers=$headerArg --attempt-deadline=300s `
    --description="Daily point-in-time ledger record (write-once)" | Out-Null
}
Write-Host "  OK $name @ $Schedule UTC -> $uri"

Write-Host ""
Write-Host "Verify:" -ForegroundColor Green
Write-Host "  gcloud scheduler jobs run $name --location=$Region --project=$ProjectId"
Write-Host "  curl https://kostolany-watch.web.app/api/ledger"
Write-Host ""
Write-Host "Re-running the job is safe: a recorded day is never overwritten." -ForegroundColor DarkGray
