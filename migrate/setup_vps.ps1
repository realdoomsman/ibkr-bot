# IBKR bot fleet - VPS setup script
# Turns a fresh Windows machine with C:\tech\ibkr copied onto it into a
# running clone of the home setup: Python venv + all 12 scheduled jobs.
#
# Run:  powershell -ExecutionPolicy Bypass -File C:\tech\ibkr\migrate\setup_vps.ps1
# Safe to re-run (idempotent) - it recreates tasks and reuses the venv.

$ErrorActionPreference = "Stop"
$Root = "C:\tech\ibkr"
if (-not (Test-Path "$Root\config.yaml")) {
    Write-Host "ERROR: $Root\config.yaml not found - copy the project folder first." -ForegroundColor Red
    exit 1
}
Set-Location $Root

# ---- 1. Python ------------------------------------------------------------
$py = $null
foreach ($cand in @("$Root\.venv\Scripts\python.exe",
                    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                    "$env:ProgramFiles\Python312\python.exe")) {
    if (Test-Path $cand) { $py = $cand; break }
}
if (-not $py) {
    # Direct download from python.org - winget does not exist on Windows Server.
    Write-Host "Downloading Python 3.12 from python.org..." -ForegroundColor Cyan
    $inst = "$env:TEMP\python-3.12.10-amd64.exe"
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe" -OutFile $inst
    Write-Host "Installing Python (silent, ~2 min)..." -ForegroundColor Cyan
    Start-Process $inst -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1" -Wait
    foreach ($cand in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
                        "$env:ProgramFiles\Python312\python.exe")) {
        if (Test-Path $cand) { $py = $cand; break }
    }
    if (-not $py) {
        Write-Host "ERROR: Python install not found. Install Python 3.12 from python.org (check 'Add to PATH'), then re-run." -ForegroundColor Red
        exit 1
    }
}

# ---- 2. Virtual environment + dependencies --------------------------------
if (-not (Test-Path "$Root\.venv\Scripts\python.exe")) {
    Write-Host "Creating venv..." -ForegroundColor Cyan
    & $py -m venv "$Root\.venv"
}
$venvPy = "$Root\.venv\Scripts\python.exe"
Write-Host "Installing dependencies (a few minutes)..." -ForegroundColor Cyan
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r "$Root\migrate\requirements.txt" --quiet
Write-Host "Dependencies installed." -ForegroundColor Green

# ---- 3. Scheduled jobs (exact clone of the home fleet) --------------------
$weekdays = @("Monday","Tuesday","Wednesday","Thursday","Friday")
# name, script args, HH:mm, days ("MF"=weekdays, "Fri", "Daily"), limit minutes (0 = unlimited)
$jobs = @(
    @("IBKR Basket Rotation",    "run_rotate.py",              "07:45", "MF",    30),
    @("IBKR Dashboard",          "dashboard.py",               "07:55", "Daily",  0),
    @("IBKR News Watcher",       "run_news.py",                "08:00", "MF",   780),
    @("IBKR Penny Radar",        "run_pennyradar.py",          "08:20", "MF",   480),
    @("IBKR Intraday Paper Bot", "run_intraday.py",            "09:28", "MF",   480),
    @("IBKR Penny Momentum",     "run_pennymo.py",             "09:28", "MF",   480),
    @("IBKR Overnight Sell",     "run_overnight.py --leg sell","09:31", "MF",    40),
    @("IBKR LastHour Sleeve",    "run_lasthour.py",            "15:28", "MF",    40),
    @("IBKR Overnight Buy",      "run_overnight.py --leg buy", "15:57", "MF",    40),
    @("IBKR Paper Trading Bot",  "run_live.py",                "16:35", "MF",    30),
    @("IBKR Daily Review",       "run_review.py",              "16:50", "MF",    15),
    @("IBKR Penny Brain",        "run_pennybrain.py",          "17:15", "Fri",   60),
    @("IBKR Research Lab",       "run_lab.py",                 "18:30", "MF",   240)
)
foreach ($j in $jobs) {
    $name, $script, $at, $days, $limitMin = $j
    $action = New-ScheduledTaskAction -Execute $venvPy -Argument $script -WorkingDirectory $Root
    $trigger = switch ($days) {
        "MF"    { New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At $at }
        "Fri"   { New-ScheduledTaskTrigger -Weekly -DaysOfWeek Friday -At $at }
        "Daily" { New-ScheduledTaskTrigger -Daily -At $at }
    }
    $limit = if ($limitMin -eq 0) { New-TimeSpan -Seconds 0 } else { New-TimeSpan -Minutes $limitMin }
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 10) -ExecutionTimeLimit $limit
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host ("  registered {0,-26} {1}  {2}" -f $name, $at, $days) -ForegroundColor Green
}

# Watchdog: every 30 min through 16:17 (7h35m window so the after-close
# check actually fires) + Sunday 20:00 login check. Registered with S4U so
# it runs even with NO user session - it must survive exactly the failures
# it watches for (a session-bound watchdog dies with the session).
$wd1 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "09:17"
$rep = New-ScheduledTaskTrigger -Once -At "09:17" -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Minutes 455)
$wd1.Repetition = $rep.Repetition
$wd2 = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At "20:00"
$wdSet = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$wdPrin = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName "IBKR Watchdog" -Action (New-ScheduledTaskAction -Execute $venvPy -Argument "run_watchdog.py" -WorkingDirectory $Root) -Trigger $wd1, $wd2 -Settings $wdSet -Principal $wdPrin -Force | Out-Null
Write-Host "  registered IBKR Watchdog           9:17-16:52 every 30min + Sun 20:00 (S4U)" -ForegroundColor Green

# ---- 4. What the script cannot do for you ---------------------------------
Write-Host ""
Write-Host "=== SETUP COMPLETE - remaining MANUAL steps ===" -ForegroundColor Yellow
Write-Host "1. Install TWS (interactivebrokers.com -> Trader Workstation) and log in"
Write-Host "   with your PAPER account. YOU type the credentials - never share them."
Write-Host "2. TWS Global Configuration:"
Write-Host "   - API > Settings: Enable ActiveX and Socket Clients, port 7497,"
Write-Host "     UNCHECK Read-Only API, trusted IP 127.0.0.1"
Write-Host "   - API > Precautions: check 'Bypass Order Precautions for API Orders'"
Write-Host "   - Lock and Exit: AUTO RESTART (not auto logoff), restart time ~23:00"
Write-Host "3. Verify:  .venv\Scripts\python.exe run_pennymo.py --once"
Write-Host "4. On your HOME PC, disable the old fleet so two copies never fight:"
Write-Host "   Get-ScheduledTask *IBKR* | Disable-ScheduledTask"
Write-Host "5. DISCONNECT the RDP session when done - do not Log Off (tasks run in it)."
