"""Risk controls - the part of a trading bot that actually keeps you solvent.

Every rule here is a circuit breaker between a buggy strategy (or a bad
market day) and your account. The strategy proposes; risk disposes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .config import RiskConfig

log = logging.getLogger("bot.risk")

# Touch this file (create it, empty) to halt all new trading immediately.
KILL_SWITCH_FILE = Path("KILL_SWITCH")


class RiskManager:
    def __init__(self, cfg: RiskConfig, day_start_equity: float):
        self.cfg = cfg
        self.day_start_equity = day_start_equity

    # ---- pre-trade checks -------------------------------------------------

    def can_open_position(self, current_equity: float, open_positions: int) -> tuple[bool, str]:
        """All checks a new BUY must pass. Returns (allowed, reason)."""
        if KILL_SWITCH_FILE.exists():
            return False, f"kill switch file present ({KILL_SWITCH_FILE.resolve()})"
        if self.daily_loss_exceeded(current_equity):
            return False, (
                f"daily loss limit hit: equity {current_equity:,.2f} vs "
                f"day start {self.day_start_equity:,.2f} "
                f"(limit {self.cfg.max_daily_loss_pct:.1%})"
            )
        if self.daily_gain_reached(current_equity):
            return False, (
                f"daily profit target hit (+{self.cfg.max_daily_gain_pct:.0%} "
                f"of capital) - done trading for the day (user directive)"
            )
        if open_positions >= self.cfg.max_open_positions:
            return False, f"max open positions reached ({self.cfg.max_open_positions})"
        return True, "ok"

    def effective_capital(self, equity: float) -> float:
        """The capital the bot is allowed to act as if it has.

        capital_base simulates the real deployment size (e.g. $20k) inside a
        larger paper account, so position sizes, loss limits, and cost drag
        all rehearse at true scale.
        """
        cb = self.cfg.capital_base
        return min(equity, cb) if cb and cb > 0 else equity

    def position_size(self, equity: float, price: float) -> int:
        """Shares to buy: a fixed fraction of effective capital, never more."""
        if price <= 0:
            return 0
        return int((self.effective_capital(equity) * self.cfg.max_position_pct) // price)

    # ---- circuit breakers -------------------------------------------------

    def daily_loss_exceeded(self, current_equity: float) -> bool:
        if self.cfg.max_daily_loss_pct <= 0:
            return False  # breaker disabled by config (user choice, 2026-08-04)
        if self.day_start_equity <= 0:
            return False
        # Dollar loss measured against effective capital: with a $20k base,
        # a $400 loss (2%) trips the breaker even inside a $1M paper account.
        loss = self.day_start_equity - current_equity
        base = self.effective_capital(self.day_start_equity)
        if base > 0 and loss >= self.cfg.max_daily_loss_pct * base:
            log.warning(
                "Daily loss circuit breaker tripped: down $%s (%.2f%% of $%s capital)",
                f"{loss:,.0f}", 100 * loss / base, f"{base:,.0f}",
            )
            return True
        return False

    def daily_gain_reached(self, current_equity: float) -> bool:
        """Profit lockout (user directive 2026-08-05): quit while ahead.

        Once today's gain reaches max_daily_gain_pct of effective capital,
        new entries stop for the day. Exits, repairs, and the end-of-day
        flatten are never blocked by this - only fresh risk is."""
        if self.cfg.max_daily_gain_pct <= 0 or self.day_start_equity <= 0:
            return False
        gain = current_equity - self.day_start_equity
        base = self.effective_capital(self.day_start_equity)
        if base > 0 and gain >= self.cfg.max_daily_gain_pct * base:
            log.info(
                "Daily profit target reached: up $%s (%.1f%% of $%s capital) - "
                "no new entries today",
                f"{gain:,.0f}", 100 * gain / base, f"{base:,.0f}",
            )
            return True
        return False

    def sanity_check_order(self, symbol: str, qty: int, price: float, equity: float) -> tuple[bool, str]:
        """Last line of defense against fat-finger / buggy orders."""
        if qty <= 0:
            return False, "quantity must be positive"
        notional = qty * price
        max_notional = self.effective_capital(equity) * self.cfg.max_position_pct * 1.05
        if notional > max_notional:
            return False, (
                f"order for {symbol} is ${notional:,.0f}, exceeds per-position "
                f"cap of ${max_notional:,.0f}"
            )
        return True, "ok"
