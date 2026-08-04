# Cloud Scheduler for browser Web Push daily regime metrics.
# Pauses legacy email newsletter jobs.
param(
  [string]$ProjectId = "kostolany-watch",
  [string]$Region = "asia-northeast3",
  [string]$Service = "kostolany-api",
  [string]$HourlySchedule = "5 * * * *",
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

if (-not $env:NEWSLETTER_CRON_SECRET) { throw "NEWSLETTER_CRON_SECRET missing" }
if (-not $env:VAPID_PUBLIC_KEY -or -not $env:VAPID_PRIVATE_KEY) {
  throw "VAPID keys missing — run: python scripts/generate_vapid_keys.py --write-env"
}

Write-Host "== Enable APIs ==" -ForegroundColor Cyan
gcloud services enable cloudscheduler.googleapis.com run.googleapis.com --project=$ProjectId --quiet

Write-Host "== Patch Cloud Run env ==" -ForegroundColor Cyan
gcloud run services update $Service `
  --region $Region `
  --project $ProjectId `
  --update-env-vars "NEWSLETTER_CRON_SECRET=$($env:NEWSLETTER_CRON_SECRET),NEWSLETTER_SITE_URL=https://kostolany-watch.web.app,VAPID_PUBLIC_KEY=$($env:VAPID_PUBLIC_KEY),VAPID_PRIVATE_KEY=$($env:VAPID_PRIVATE_KEY),VAPID_MAILTO=$($env:VAPID_MAILTO)" `
  --quiet

function Upsert-HttpJob([string]$Name, [string]$Uri, [string]$Schedule, [string]$Desc) {
  $headerArg = "Content-Type=application/json,X-Cron-Secret=$($env:NEWSLETTER_CRON_SECRET)"
  $ErrorActionPreference = "Continue"
  gcloud scheduler jobs describe $Name --location=$Region --project=$ProjectId 2>$null | Out-Null
  $exists = ($LASTEXITCODE -eq 0)
  $ErrorActionPreference = "Stop"
  if ($exists) {
    gcloud scheduler jobs update http $Name `
      --location=$Region --project=$ProjectId `
      --schedule=$Schedule --time-zone=$TimeZone `
      --uri=$Uri --http-method=POST `
      --update-headers=$headerArg --attempt-deadline=180s | Out-Null
  } else {
    gcloud scheduler jobs create http $Name `
      --location=$Region --project=$ProjectId `
      --schedule=$Schedule --time-zone=$TimeZone `
      --uri=$Uri --http-method=POST `
      --headers=$headerArg --attempt-deadline=180s `
      --description=$Desc | Out-Null
  }
  Write-Host "  OK $Name @ $Schedule → $Uri"
}

function Pause-Job([string]$Name) {
  $ErrorActionPreference = "Continue"
  gcloud scheduler jobs pause $Name --location=$Region --project=$ProjectId 2>$null | Out-Null
  $ErrorActionPreference = "Stop"
  Write-Host "  paused $Name (if existed)"
}

Write-Host "== Pause legacy newsletter jobs ==" -ForegroundColor Cyan
Pause-Job "newsletter-daily-generate"
Pause-Job "newsletter-weekly-dispatch"

Write-Host "== Upsert push dispatch job ==" -ForegroundColor Cyan
Upsert-HttpJob "push-daily-dispatch" "https://kostolany-watch.web.app/api/push/dispatch" $HourlySchedule "Hourly Web Push (filters by hour_kst)"

Write-Host "Push scheduler ready. Email newsletter paused." -ForegroundColor Green
