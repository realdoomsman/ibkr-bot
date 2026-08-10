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
    # No ?. operator here: the scheduled task runs Windows PowerShell 5.1,
    # which parses null-conditionals as errors (cosmetic, but noisy).
    $b = if ($before) { $before.Substring(0, 7) } else { "?" }
    $a = if ($after) { $after.Substring(0, 7) } else { "?" }
    Write-Host "Code updated: $b -> $a" -ForegroundColor Green
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
