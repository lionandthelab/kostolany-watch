# Enable Cloud Scheduler jobs for newsletter (daily auto card).
# Weekly Claude briefs run on the LOCAL PC via Task Scheduler — see register-weekly-brief-task.ps1
param(
  [string]$ProjectId = "kostolany-watch",
  [string]$Region = "asia-northeast3",
  [string]$Service = "kostolany-api",
  # Daily 22:00 KST = 13:00 UTC
  [string]$DailySchedule = "0 13 * * *",
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
  $bytes = New-Object byte[] 24
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $env:NEWSLETTER_CRON_SECRET = [Convert]::ToBase64String($bytes)
  Add-Content -Path ".env" -Value "`nNEWSLETTER_CRON_SECRET=$($env:NEWSLETTER_CRON_SECRET)" -Encoding utf8
  Write-Host "Generated NEWSLETTER_CRON_SECRET and appended to .env" -ForegroundColor Yellow
}

if (-not $env:RESEND_API_KEY) {
  Write-Host "WARN: RESEND_API_KEY missing — daily email will fail until set." -ForegroundColor Yellow
}

Write-Host "== Enable APIs ==" -ForegroundColor Cyan
gcloud services enable cloudscheduler.googleapis.com run.googleapis.com --project=$ProjectId --quiet

Write-Host "== Patch Cloud Run env ==" -ForegroundColor Cyan
$envPairs = @(
  "NEWSLETTER_CRON_SECRET=$($env:NEWSLETTER_CRON_SECRET)",
  "NEWSLETTER_SITE_URL=https://kostolany-watch.web.app"
)
if ($env:RESEND_API_KEY) { $envPairs += "RESEND_API_KEY=$($env:RESEND_API_KEY)" }
if ($env:RESEND_FROM) { $envPairs += "RESEND_FROM=$($env:RESEND_FROM)" }

gcloud run services update $Service `
  --region $Region `
  --project $ProjectId `
  --update-env-vars (($envPairs -join ",")) `
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
      --update-headers=$headerArg --attempt-deadline=300s | Out-Null
  } else {
    gcloud scheduler jobs create http $Name `
      --location=$Region --project=$ProjectId `
      --schedule=$Schedule --time-zone=$TimeZone `
      --uri=$Uri --http-method=POST `
      --headers=$headerArg --attempt-deadline=300s `
      --description=$Desc | Out-Null
  }
  Write-Host "  OK $Name @ $Schedule → $Uri"
}

Write-Host "== Upsert Cloud Scheduler jobs ==" -ForegroundColor Cyan
$dailyUri = "https://kostolany-watch.web.app/api/briefs/daily/generate?dispatch=true"
Upsert-HttpJob "newsletter-daily-generate" $dailyUri $DailySchedule "Daily regime card generate + email"

# Keep legacy Friday dispatch as safety net for weekly GCS briefs (no-op if already sent)
$weeklyUri = "https://kostolany-watch.web.app/api/newsletter/dispatch?kind=weekly"
Upsert-HttpJob "newsletter-weekly-dispatch" $weeklyUri "0 1 * * 5" "Weekly brief email backup (Fri 10:00 KST)"

Write-Host ""
Write-Host "Cloud jobs:" -ForegroundColor Green
Write-Host "  Daily card: Cloud Scheduler (PC off OK)"
Write-Host "  Weekly Claude essay: LOCAL Task Scheduler — run scripts/register-weekly-brief-task.ps1"
