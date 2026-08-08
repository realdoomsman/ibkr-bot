# VM auto-update: pull the newest code from the private GitHub repo.
# Registered as "IBKR Auto-Update" (weekdays 18:05, after the day is done).
#
# Safe by construction:
#   - state files (journal, ladders, ledgers, alerts.yaml) are .gitignored,
#     so a pull can NEVER overwrite the VM's live records
#   - --ff-only: refuses anything but a clean fast-forward
#   - never runs during market hours (the schedule enforces it)

$Root = "C:\tech\ibkr"
Set-Location $Root
$before = git rev-parse HEAD 2>$null
git pull --ff-only 2>&1 | Write-Host
$after = git rev-parse HEAD 2>$null
if ($before -ne $after) {
    Write-Host "Code updated: $($before?.Substring(0,7)) -> $($after?.Substring(0,7))" -ForegroundColor Green
    & "$Root\.venv\Scripts\python.exe" -m pip install -r "$Root\migrate\requirements.txt" --quiet
    # The dashboard keeps serving old code until restarted:
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match "dashboard\.py" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false }
    Start-ScheduledTask "IBKR Dashboard" -ErrorAction SilentlyContinue
    Write-Host "Dependencies refreshed, dashboard restarted." -ForegroundColor Green
} else {
    Write-Host "Already up to date."
}
