# Auto-updates: home -> GitHub -> VM, no more zip-copying

The stale-VM blackout happened because code traveled by hand. This replaces
the zip ritual with git: improvements get committed at home, pushed to a
PRIVATE GitHub repo, and the VM pulls them automatically every evening at
18:05. State files (journal, ladders, ledgers, alerts.yaml) are .gitignored,
so a pull can never touch the VM's live records.

## One-time setup - your part (~10 minutes)

### 1. Create the private repo (home PC, browser)
- github.com -> New repository -> name: `ibkr-bot` -> **Private** -> Create
  (no README/gitignore - the project already has them)

### 2. Push the code (home PC, PowerShell in C:\tech\ibkr)
The local repo is already initialized and committed. Connect and push
(replace YOURNAME; sign in when the Git window prompts YOU - never share
credentials with anyone):

    git remote add origin https://github.com/YOURNAME/ibkr-bot.git
    git push -u origin main

### 3. Hook up the VM (RDP, PowerShell)
Install Git for Windows silently:

    Invoke-WebRequest -Uri "https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/Git-2.47.1-64-bit.exe" -OutFile "$env:TEMP\git-setup.exe"
    Start-Process "$env:TEMP\git-setup.exe" -ArgumentList "/VERYSILENT /NORESTART" -Wait

Then turn C:\tech\ibkr into a clone WITHOUT touching its state files
(git only manages tracked files; everything stateful is ignored):

    cd C:\tech\ibkr
    git init -b main
    git remote add origin https://github.com/YOURNAME/ibkr-bot.git
    git fetch origin
    git reset --hard origin/main

(A sign-in window appears for the private repo - that's GitHub asking YOU.)

### 4. Register the nightly pull (VM, same PowerShell)

    Register-ScheduledTask -TaskName "IBKR Auto-Update" -Action (New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-ExecutionPolicy Bypass -File C:\tech\ibkr\run_deploy.ps1" -WorkingDirectory "C:\tech\ibkr") -Trigger (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "18:05") -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20)) -Force

## Ongoing flow (after setup)

- Claude improves code at home -> commits -> you run `git push` (one command)
- VM pulls it at 18:05 automatically - never during market hours
- The zip ritual dies; migrate/ stays as the disaster-recovery path
