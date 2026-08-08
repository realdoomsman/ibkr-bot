# Re-register the "IBKR Watchdog" task with the review-hardened settings:
#   - repetition window extended so the after-close (16:05+) check fires
#   - S4U logon: runs even when no RDP session exists (survives reboots
#     and logoffs - a session-bound watchdog dies with the session)
# Run on the VM in an ADMIN PowerShell:
#   powershell -ExecutionPolicy Bypass -File C:\tech\ibkr\register_watchdog.ps1

$py = "C:\tech\ibkr\.venv\Scripts\python.exe"
$wd1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "09:17"
$rep = New-ScheduledTaskTrigger -Once -At "09:17" -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Minutes 455)
$wd1.Repetition = $rep.Repetition
$wd2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "20:00"
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$prin = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName "IBKR Watchdog" `
    -Action (New-ScheduledTaskAction -Execute $py -Argument "run_watchdog.py" -WorkingDirectory "C:\tech\ibkr") `
    -Trigger $wd1, $wd2 -Settings $set -Principal $prin -Force
Write-Host "IBKR Watchdog re-registered: weekdays 9:17-16:52 every 30 min + Sunday 20:00, survives logoff (S4U)" -ForegroundColor Green
