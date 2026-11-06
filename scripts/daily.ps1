# Daily ingest used by Windows Task Scheduler. Writes one log file per day.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("daily-{0}.log" -f (Get-Date -Format "yyyy-MM"))
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
"[$stamp] start" | Add-Content -LiteralPath $log -Encoding utf8
python run.py daily *>> $log
"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] finish" | Add-Content -LiteralPath $log -Encoding utf8
