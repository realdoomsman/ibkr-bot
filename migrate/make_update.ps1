# Build ibkr_update.zip: CODE + MODELS ONLY - safe to extract over the VM's
# C:\tech\ibkr without touching its live trading records.
#
# Excluded on purpose (the VM's copies are the real ones):
#   journal.csv, trades/equity history, pennymo_state.json, penny_radar.csv,
#   penny_predictions.csv, filter_scoreboard.csv, news_seen.json, all *.log,
#   reviews.log, latest_review.txt, data_cache, data_archive, screens_archive
#
# Run on the home PC:   powershell -ExecutionPolicy Bypass -File migrate\make_update.ps1
# Then on the VM:       copy zip over, and:
#   Expand-Archive -Path "$env:USERPROFILE\Desktop\ibkr_update.zip" -DestinationPath C:\tech\ibkr -Force

$Root = "C:\tech\ibkr"
$stage = Join-Path $env:TEMP "ibkr_update_stage"
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
robocopy $Root $stage /E `
    /XD .venv data_cache data_archive screens_archive __pycache__ migrate `
    /XF *.pyc *.tmp *.log journal.csv trades_history.csv equity_history.csv `
        pennymo_state.json penny_radar.csv penny_predictions.csv `
        filter_scoreboard.csv filter_trades.csv news_seen.json reviews.log `
        latest_review.txt lab_ledger.csv latest_lab.txt pennybrain_report.txt `
        pennymo_state.json.corrupt screen_results.csv `
        health.json watchdog_state.json heartbeat_*.txt `
        toxicity.json tripwire_seen*.json | Out-Null
if ($LASTEXITCODE -ge 8) { Write-Host "robocopy failed: $LASTEXITCODE" -ForegroundColor Red; exit 1 }
$zip = Join-Path $Root "migrate\ibkr_update.zip"
Compress-Archive -Path "$stage\*" -DestinationPath $zip -Force
Write-Host ("update bundle: {0}  ({1:N1} MB)" -f $zip, ((Get-Item $zip).Length/1MB)) -ForegroundColor Green
Write-Host "NOTE: after extracting on the VM, restart the dashboard if it is running"
Write-Host "(old process keeps serving the old page until killed or the VM reboots)."
