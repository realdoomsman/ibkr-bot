# Optional upgrade: fully automatic TWS login (kills the Sunday chore)

Today the fleet depends on ONE weekly human act: logging into TWS after
IBKR's Sunday session reset. IBC (github.com/IbcAlpha/IBC - the standard
open-source tool for this, used by unattended IBKR setups everywhere)
automates the login itself, so the machine needs no human at all.

Trade-off to decide with open eyes: IBC needs your IBKR username and
password written into a config file ON THE VM (`config.ini`). That file
never leaves the machine, but it is your paper login stored in plain text
on a cloud server. For a PAPER account this is a reasonable trade; if the
account ever went live, revisit this decision explicitly.

## Setup sketch (~30 min, all on the VM; do it any evening)

1. Download IBC's latest Windows release zip from
   github.com/IbcAlpha/IBC/releases -> extract to C:\IBC
2. Edit C:\IBC\config.ini - the important lines:
       IbLoginId=YOUR_PAPER_USERNAME        <- you type these, only here
       IbPassword=YOUR_PASSWORD
       TradingMode=paper
       AcceptIncomingConnectionAction=accept
       ExistingSessionDetectedAction=primaryoverride
       ReloginAfterSecondFactorAuthenticationTimeout=yes
       TwofaTimeoutAction=restart
3. Point IBC's StartTWS.bat at the installed TWS version (TWS_MAJOR_VRSN in
   the bat file must match what's in C:\Jts).
4. Scheduled task "IBKR Gateway Login": run StartTWS.bat at boot AND daily
   ~08:30, "run only if not already running".
5. Keep TWS's own Auto restart at 11 PM as the second layer.

Result: VM reboots, Windows updates, Sunday resets - the login heals
itself, and the watchdog verifies it did. The human becomes optional.

Not set up yet - this file is the recipe for when you want it.
