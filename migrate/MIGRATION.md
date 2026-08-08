# Moving the bot fleet to a VPS (virtual computer)

The goal: the 12-job fleet runs on a rented Windows machine that is on 24/7,
so trading no longer depends on your personal PC being awake.

## Step 0 - Rent the VPS (only you can do this part)

Any Windows VPS/cloud VM works. What to ask for:
- **Windows Server 2022** (or Windows 10/11)
- **2 vCPU, 8 GB RAM** (TWS is a Java app and eats memory; 4 GB is the bare
  minimum, 8 GB is comfortable), ~80 GB disk
- Region: anywhere in the US is fine - our bots scan every 5 minutes, so
  latency does not matter (this is not high-frequency trading)

Typical options (~$25-50/month): AWS Lightsail (Windows), Azure B2ms,
Vultr / Contabo / any "Windows VPS" host. Create the account and pay
yourself - never give credentials or card details to anyone, including me.

## Step 1 - Connect

Use **Remote Desktop** (RDP) from your PC: Start > Remote Desktop Connection,
enter the VPS IP + the username/password from the provider.

## Step 2 - Copy the project

Copy `ibkr_bundle.zip` (in this folder) to the VPS - easiest way: in the RDP
window you can copy-paste files directly. Extract it so the code lands at
**C:\tech\ibkr** exactly (paths are baked into the scheduled jobs).

## Step 3 - Run the setup script

On the VPS, open PowerShell and run:

    powershell -ExecutionPolicy Bypass -File C:\tech\ibkr\migrate\setup_vps.ps1

It installs Python, rebuilds the environment, and registers all 12 scheduled
jobs identical to home. Takes ~10 minutes.

## Step 4 - Install and configure TWS (manual, ~10 minutes)

1. Download Trader Workstation from interactivebrokers.com and install.
2. Log in with your **paper** account. (IBKR allows one login per username at
   a time - logging in here logs out your home TWS. That's expected.)
3. Global Configuration:
   - **API > Settings**: Enable ActiveX and Socket Clients; Socket port
     **7497**; uncheck **Read-Only API**; add trusted IP **127.0.0.1**
   - **API > Precautions**: check **Bypass Order Precautions for API Orders**
   - **Lock and Exit**: choose **Auto restart**, set restart time ~23:00
     (this is what keeps TWS alive day after day)

## Step 5 - Verify, then hand over

    C:\tech\ibkr\.venv\Scripts\python.exe C:\tech\ibkr\run_pennymo.py --once

Expect "Connected" + "Penny sleeve up". Then:
- Dashboard: open http://localhost:8050 inside the RDP session (do NOT
  expose port 8050 to the internet).
- On your HOME PC, disable the old fleet: `Get-ScheduledTask *IBKR* | Disable-ScheduledTask`
- **Disconnect** the RDP session (click the X). Do **not** Log Off - the
  session must stay alive for TWS and the tasks.

## Shipping updates later (after the first migration)

Code changes are developed and tested on the home PC, then shipped:

1. Home PC: `powershell -ExecutionPolicy Bypass -File migrate\make_update.ps1`
   -> builds `migrate\ibkr_update.zip` (code + models ONLY - it can never
   overwrite the VM's live journal, ladder state, or histories).
2. Copy the zip to the VM desktop (paste into the RDP window).
3. VM PowerShell:
   `Expand-Archive -Path "$env:USERPROFILE\Desktop\ibkr_update.zip" -DestinationPath C:\tech\ibkr -Force`
4. If the dashboard is running, restart it (kill the python `dashboard.py`
   process or reboot the VM overnight) - a running dashboard keeps serving
   the OLD page until restarted. The trading bots pick up new code on their
   next scheduled start automatically.

## Ongoing

- Set Windows Update active hours to market hours so reboots land at night.
- If you set an Anthropic key for the AI news reads, do it on the VPS:
  `setx ANTHROPIC_API_KEY "..."` (type it yourself).
- State moves with the folder: journal.csv, pennymo_state.json, models/,
  equity/trade history - the fleet resumes exactly where it left off.
