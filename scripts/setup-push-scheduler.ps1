# Cloud Scheduler for browser Web Push daily regime metrics.
# Pauses legacy email newsletter jobs.
param(
  [string]$ProjectId = "kostolany-watch",
  [string]$Region = "asia-northeast3",
  [string]$Service = "kostolany-api",
  [string]$HourlySchedule = "5 * * * *",
  [string]$DailyCardSchedule = "0 13 * * *",
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

function Delete-Job([string]$Name) {
  $ErrorActionPreference = "Continue"
  gcloud scheduler jobs delete $Name --location=$Region --project=$ProjectId --quiet 2>$null | Out-Null
  $ErrorActionPreference = "Stop"
  Write-Host "  deleted $Name (if existed)"
}

# Only `newsletter-weekly-dispatch` is a newsletter job. `newsletter-daily-generate`
# was misread as one here and paused along with it on 2026-08-02 — but it builds the
# on-site daily card and sends no email, so pausing it silently froze the newest card
# at 2026-08-02 while the desk kept serving. It is replaced below under a name that
# says what it does; the old job is retired rather than left paused-and-misleading.
Write-Host "== Retire legacy newsletter jobs ==" -ForegroundColor Cyan
Pause-Job "newsletter-weekly-dispatch"
Delete-Job "newsletter-daily-generate"

Write-Host "== Upsert push dispatch job ==" -ForegroundColor Cyan
Upsert-HttpJob "push-daily-dispatch" "https://kostolany-watch.web.app/api/push/dispatch" $HourlySchedule "Hourly Web Push (filters by hour_kst)"

# 13:00 UTC = 22:00 KST the same calendar day. The card's slug is `daily-<date>`
# built from the server's UTC date, so this slot is the one where UTC and KST
# agree — see the endpoint docstring before moving it.
Write-Host "== Upsert daily card job ==" -ForegroundColor Cyan
Upsert-HttpJob "briefs-daily-generate" "https://kostolany-watch.web.app/api/briefs/daily/generate" $DailyCardSchedule "Daily desk card at 22:00 KST (on-site only, no email)"

Write-Host "Push scheduler ready. Daily card restored. Email newsletter retired." -ForegroundColor Green
