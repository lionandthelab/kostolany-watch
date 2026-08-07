# Enable the watch payload refresh job (Cloud Scheduler -> Cloud Run).
#
# Why this job exists: the service runs at minScale=1 with CPU throttling off,
# so the instance never restarts and never re-warms, and the only other rebuild
# trigger is a user hitting /api/watch. Three days of zero traffic left the
# 6h-TTL payload serving 68h stale (measured 2026-08-07).
param(
  [string]$ProjectId = "kostolany-watch",
  [string]$Region = "asia-northeast3",
  [string]$Service = "kostolany-api",
  # Every 4h against a 6h cache TTL: one missed run still lands inside TTL, two
  # consecutive misses do not — which is exactly when /api/health/freshness
  # should go red. Tightening this below ~1h would stack runs, since a full
  # two-market rebuild takes ~7 minutes.
  [string]$Schedule = "0 */4 * * *",
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

# A rebuilt payload that dies with the instance buys nothing — GCS is what makes
# the refresh outlive the revision that computed it.
Write-Host "== Check durable storage ==" -ForegroundColor Cyan
$envNames = gcloud run services describe $Service --region=$Region --project=$ProjectId `
  --format="value(spec.template.spec.containers[0].env[].name)"
if ($envNames -notmatch "GCS_CACHE_BUCKET") {
  Write-Host "ERROR: GCS_CACHE_BUCKET not set on $Service. Refreshed payloads would not survive a redeploy." -ForegroundColor Red
  exit 1
}
Write-Host "  OK cache bucket configured"

$name = "watch-refresh"
$uri = "https://kostolany-watch.web.app/api/watch/refresh"
$headerArg = "Content-Type=application/json,X-Cron-Secret=$($env:NEWSLETTER_CRON_SECRET)"

Write-Host "== Upsert Cloud Scheduler job ==" -ForegroundColor Cyan
$ErrorActionPreference = "Continue"
gcloud scheduler jobs describe $name --location=$Region --project=$ProjectId 2>$null | Out-Null
$exists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"

# 60s deadline, not 300s: the endpoint only enqueues and returns. Anything past a
# few seconds means the request never reached the handler, and a long deadline
# would just delay that signal.
if ($exists) {
  gcloud scheduler jobs update http $name `
    --location=$Region --project=$ProjectId `
    --schedule=$Schedule --time-zone=$TimeZone `
    --uri=$uri --http-method=POST `
    --update-headers=$headerArg --attempt-deadline=60s | Out-Null
} else {
  gcloud scheduler jobs create http $name `
    --location=$Region --project=$ProjectId `
    --schedule=$Schedule --time-zone=$TimeZone `
    --uri=$uri --http-method=POST `
    --headers=$headerArg --attempt-deadline=60s `
    --description="Rebuild watch payloads on a clock (traffic alone never triggers it)" | Out-Null
}
Write-Host "  OK $name @ $Schedule $TimeZone -> $uri"

Write-Host ""
Write-Host "Verify:" -ForegroundColor Green
Write-Host "  gcloud scheduler jobs run $name --location=$Region --project=$ProjectId"
Write-Host "  curl https://kostolany-watch.web.app/api/health/freshness"
Write-Host ""
Write-Host "The rebuild takes ~7 min; the endpoint returns immediately. Check freshness after, not during." -ForegroundColor DarkGray
Write-Host "Re-running is safe: an already-queued market is not enqueued twice." -ForegroundColor DarkGray
