# Dashboard used by Windows Task Scheduler at logon. Runs hidden on port 8765.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "dashboard.log"
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') starting dashboard" | Add-Content -LiteralPath $log -Encoding utf8
python run.py serve --host 0.0.0.0 --port 8765 *>> $log
