"""Run one paper-trading cycle against IBKR.

Prerequisites:
  1. IBKR account with paper trading, TWS (or IB Gateway) running and logged
     in to the PAPER account.
  2. API enabled in TWS: File > Global Configuration > API > Settings.
  3. pip install -r requirements.txt

Then:
    python run_live.py

Despite the filename, this trades your PAPER account (fake money) unless you
deliberately reconfigure the port AND set I_UNDERSTAND_LIVE_TRADING_RISKS=yes.
"""

from __future__ import annotations

import logging

from bot.config import load_config
from bot.main import run_cycle


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("bot.log", encoding="utf-8"),
        ],
    )
    cfg = load_config()  # enforces the paper-trading guard
    run_cycle(cfg)


if __name__ == "__main__":
    main()
