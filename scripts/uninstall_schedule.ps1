$ErrorActionPreference = "Stop"
foreach ($name in @("HEOR Signal Daily", "HEOR Signal Dashboard")) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed: $name"
    }
}
Write-Host "HEOR Signal scheduled tasks are removed. Your data is untouched."
