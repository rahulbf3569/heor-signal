param(
    [string]$DailyAt = "07:30",
    [switch]$NoDashboard
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 40)

$dailyAction = New-ScheduledTaskAction `
    -Execute $powershell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\scripts\daily.ps1`""
$dailyTrigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
Register-ScheduledTask `
    -TaskName "HEOR Signal Daily" `
    -Action $dailyAction `
    -Trigger $dailyTrigger `
    -Settings $settings `
    -Description "Fetch the daily HEOR brief and send it to Telegram." `
    -Force | Out-Null

Write-Host "Registered: HEOR Signal Daily at $DailyAt"

if (-not $NoDashboard) {
    $dashAction = New-ScheduledTaskAction `
        -Execute $powershell `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\scripts\dashboard.ps1`""
    $dashTrigger = New-ScheduledTaskTrigger -AtLogOn
    Register-ScheduledTask `
        -TaskName "HEOR Signal Dashboard" `
        -Action $dashAction `
        -Trigger $dashTrigger `
        -Settings $settings `
        -Description "Run the HEOR Signal dashboard at http://localhost:8765." `
        -Force | Out-Null
    Write-Host "Registered: HEOR Signal Dashboard at logon"
}

Write-Host ""
Write-Host "Check with:  Get-ScheduledTask -TaskName 'HEOR Signal*'"
Write-Host "Run now:     Start-ScheduledTask -TaskName 'HEOR Signal Daily'"
Write-Host "Remove:      .\scripts\uninstall_schedule.ps1"
