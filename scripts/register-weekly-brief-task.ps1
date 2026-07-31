# Register a Friday morning local task that runs Claude weekly brief generation.
# The PC must be ON (or wake to run). Daily cards stay on Google Cloud Scheduler.
param(
  [string]$TaskName = "KostolanyWeeklyBriefClaude",
  # Friday 09:00 local (Korea)
  [string]$Time = "09:00",
  [ValidateSet("Friday")]
  [string]$Day = "Friday"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { $Py = (Get-Command python).Source }
$Script = Join-Path $Root "scripts\generate_weekly_brief.py"
$LogDir = Join-Path $Root "artifacts\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$OutLog = Join-Path $LogDir "weekly_brief.out.log"
$ErrLog = Join-Path $LogDir "weekly_brief.err.log"

$Action = New-ScheduledTaskAction `
  -Execute $Py `
  -Argument "`"$Script`"" `
  -WorkingDirectory $Root

$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $Time
$Settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RunOnlyIfNetworkAvailable

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Description "Kostolany Watch: Claude weekly regime brief → API → email (local Claude subscription)" `
  -Force | Out-Null

Write-Host "Registered Task Scheduler job: $TaskName" -ForegroundColor Green
Write-Host "  When: every $Day at $Time (local time)"
Write-Host "  Runs: $Py $Script"
Write-Host "  NOTE: PC must be awake. Daily cards use Cloud Scheduler instead."
Write-Host "Manual test: $Py `"$Script`" --dry-run"
Write-Host "Logs (optional redirect): $OutLog / $ErrLog"
