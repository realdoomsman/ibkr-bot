"""Thin wrapper around the IBKR connection: account state and order placement.

Every order passes through the RiskManager first, and everything is logged.
Uses ib_async (the community-maintained successor to ib_insync).
"""

from __future__ import annotations

import csv
import datetime as _dt
import logging
import os
from pathlib import Path

from .config import LIVE_OPT_IN_ENV, BotConfig
from .risk import RiskManager

log = logging.getLogger("bot.broker")

JOURNAL_FILE = Path("journal.csv")
_JOURNAL_FIELDS = [
    "time", "symbol", "action", "kind", "qty", "filled",
    "signal_price", "fill_price", "note",
]


def journal(symbol: str, action: str, kind: str, qty: int = 0, filled: int = 0,
            signal_price: float = 0.0, fill_price: float = 0.0, note: str = "") -> None:
    """Append one decision/outcome row to journal.csv for the review tool.

    Journaling must NEVER break trading - swallow everything.
    """
    try:
        exists = JOURNAL_FILE.exists()
        with JOURNAL_FILE.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_JOURNAL_FIELDS)
            if not exists:
                w.writeheader()
            w.writerow({
                "time": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": symbol, "action": action, "kind": kind,
                "qty": qty, "filled": filled,
                "signal_price": round(signal_price, 4) if signal_price else "",
                "fill_price": round(fill_price, 4) if fill_price else "",
                "note": note,
            })
    except Exception:
        log.debug("journal write failed", exc_info=True)


class Broker:
    # Statuses under which an order can still execute. "PendingCancel" is
    # included deliberately: an unconfirmed cancel can still fill.
    ACTIVE_STATUSES = frozenset(
        {"PreSubmitted", "Submitted", "PendingSubmit", "ApiPending", "PendingCancel"}
    )

    def __init__(self, cfg: BotConfig, sleeve: str = "core"):
        from ib_async import IB

        self.cfg = cfg
        self.ib = IB()
        #: Which strategy sleeve this broker instance trades for - stamped
        #: into the journal so the daily review can attribute P&L per sleeve.
        self.sleeve = sleeve
        #: Avg fill price of the most recent submit_entry_limit call (0.0 if
        #: unfilled) - lets callers record a true cost basis without changing
        #: the method's return type.
        self.last_fill_price = 0.0

    # ---- connection -------------------------------------------------------

    def connect(self) -> None:
        ib_cfg = self.cfg.ib
        log.info("Connecting to IBKR at %s:%s (clientId=%s)", ib_cfg.host, ib_cfg.port, ib_cfg.client_id)
        self.ib.connect(ib_cfg.host, ib_cfg.port, clientId=ib_cfg.client_id, timeout=15)
        # Config-driven: 3 = delayed (free), 1 = real-time (paid subscription
        # + paper sharing required - see IBConfig.market_data_type).
        mdt = getattr(ib_cfg, "market_data_type", 3) or 3
        self.ib.reqMarketDataType(mdt)
        if mdt == 1:
            log.info("REAL-TIME market data requested (subscription mode)")
        accounts = self.ib.managedAccounts()
        log.info("Connected. Accounts: %s", accounts)
        # Paper account ids start with 'D' at IBKR. The port check in config.py
        # can be fooled (any port is configurable in TWS), so also enforce on
        # the account actually connected - the one signal that can't lie.
        if accounts and not all(a.startswith("D") for a in accounts):
            if os.environ.get(LIVE_OPT_IN_ENV, "").lower() != "yes":
                self.ib.disconnect()
                raise SystemExit(
                    f"REFUSING TO TRADE: connected account(s) {accounts} do not "
                    f"look like paper accounts (paper account ids start with 'D').\n"
                    f"This bot is educational. Set {LIVE_OPT_IN_ENV}=yes only if "
                    f"you truly intend live trading."
                )
            log.warning("LIVE ACCOUNT %s - trading real money!", accounts)

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    # ---- account state ----------------------------------------------------

    def net_liquidation(self) -> float:
        for v in self.ib.accountSummary():
            if v.tag == "NetLiquidation":
                return float(v.value)
        raise RuntimeError("Could not read NetLiquidation from account summary")

    def previous_day_equity(self) -> float | None:
        """IBKR's own record of yesterday's closing equity, if available.

        Used as the daily-loss baseline: comparing against equity captured at
        the start of the same run would make the circuit breaker blind to
        overnight losses.
        """
        for v in self.ib.accountSummary():
            if v.tag == "PreviousDayEquityWithLoanValue":
                try:
                    return float(v.value)
                except ValueError:
                    return None
        return None

    def position_qty(self, symbol: str) -> int:
        for pos in self.ib.positions():
            if pos.contract.symbol == symbol:
                return int(pos.position)
        return 0

    def pending_order_symbols(self, actions: tuple[str, ...] = ("BUY", "SELL")) -> set[str]:
        """Symbols with orders still working at IBKR (this run or an earlier one).

        Positions only reflect FILLS. A market order placed after the close sits
        pending until the next open, so without this check a second run the same
        evening would happily double every order. `actions` lets callers ask
        only about pending BUYs (duplicate-entry guard) without tripping over
        resting protective stops, which are SELLs by nature.
        """
        return {
            t.contract.symbol
            for t in self.ib.reqAllOpenOrders()
            if t.orderStatus.status in self.ACTIVE_STATUSES and t.order.action in actions
        }

    def cancel_symbol_orders(
        self, symbol: str, actions: tuple[str, ...] = ("BUY", "SELL"), deadline_s: float = 5.0
    ) -> tuple[bool, int]:
        """Cancel working orders for one symbol and CONFIRM the outcome.

        Returns (all_done, filled_during_cancel):
          all_done             - every targeted order reached a final state and
                                 no foreign-client orders are in the way. If
                                 False, callers must NOT proceed with an
                                 offsetting order (risk of double-sell).
          filled_during_cancel - shares that executed while we were cancelling
                                 ("too late to cancel" is routine at IBKR);
                                 callers must re-read the live position.
        """
        targeted = [
            t for t in self.ib.reqAllOpenOrders()
            if t.contract.symbol == symbol
            and t.order.action in actions
            and t.orderStatus.status in self.ACTIVE_STATUSES
        ]
        my_id = self.ib.client.clientId
        own = [t for t in targeted if t.order.clientId == my_id]
        foreign = [t for t in targeted if t.order.clientId != my_id]
        if foreign:
            # We cannot cancel another client's orders (order-id spaces are
            # per-client). Proceeding would risk a double-sell - surface it.
            log.critical(
                "%s has working %s order(s) from OTHER client ids %s - cannot "
                "manage them from here; aborting this action. Resolve in TWS.",
                symbol, actions, sorted({t.order.clientId for t in foreign}),
            )

        for t in own:
            log.info("Cancelling working %s order for %s", t.order.action, symbol)
            self.ib.cancelOrder(t.order)

        waited = 0.0
        while own and not all(t.isDone() for t in own) and waited < deadline_s:
            self.ib.sleep(0.25)
            waited += 0.25

        filled_during = sum(int(t.orderStatus.filled) for t in own)
        all_done = not foreign and all(t.isDone() for t in own)
        if not all_done:
            log.error("%s: cancel NOT fully confirmed within %.0fs", symbol, deadline_s)
        return all_done, filled_during

    def buy_to_cover(self, symbol: str, qty: int):
        """Repair order: close an accidental short immediately. Bypasses entry
        gates on purpose - covering a short REDUCES risk and must never be
        blocked by position caps or breakers."""
        from ib_async import MarketOrder, Stock

        log.error("REPAIR: buying to cover short of %d %s", qty, symbol)
        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        trade = self.ib.placeOrder(contract, MarketOrder("BUY", qty))
        self.ib.sleep(5)
        log.info("REPAIR STATUS: %s (filled %s)", trade.orderStatus.status, trade.orderStatus.filled)
        journal(symbol, "BUY", "repair_cover", qty=qty,
                filled=int(trade.orderStatus.filled),
                fill_price=float(trade.orderStatus.avgFillPrice or 0))
        return trade

    def open_position_count(self) -> int:
        """Held positions plus symbols with pending orders (union, deduplicated)."""
        held = {p.contract.symbol for p in self.ib.positions() if p.position != 0}
        return len(held | self.pending_order_symbols())

    # ---- orders -----------------------------------------------------------

    def submit_entry_limit(
        self,
        symbol: str,
        qty: int,
        risk: RiskManager,
        last_price: float,
        cap_pct: float = 0.005,
        wait_s: int = 8,
        cancel_unfilled: bool = True,
    ) -> int:
        """Risk-gated BUY via a capped (marketable) limit order.

        The limit sits `cap_pct` above the last price: it fills like a market
        order in normal conditions but can never chase a spike more than the
        cap - vital in fast low-priced names. With `cancel_unfilled` the
        remainder is cancelled after `wait_s` and the next cycle retries
        (intraday mode); without it the DAY order rests - required for the
        daily bot, whose after-close orders fill at the next open.
        Returns shares filled so far.
        """
        from ib_async import LimitOrder, Stock

        equity = self.net_liquidation()
        allowed, reason = risk.can_open_position(equity, self.open_position_count())
        if not allowed:
            log.warning("BLOCKED BUY %s x%d: %s", symbol, qty, reason)
            journal(symbol, "BUY", "blocked_entry", qty=qty, signal_price=last_price, note=reason)
            return 0
        ok, reason = risk.sanity_check_order(symbol, qty, last_price, equity)
        if not ok:
            log.warning("BLOCKED BUY %s x%d: %s", symbol, qty, reason)
            journal(symbol, "BUY", "blocked_entry", qty=qty, signal_price=last_price, note=reason)
            return 0

        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        limit = round(last_price * (1 + cap_pct), 2)
        trade = self.ib.placeOrder(contract, LimitOrder("BUY", qty, limit))
        log.info("ORDER SUBMITTED: BUY %d %s (limit %.2f, cap %.1f%%)",
                 qty, symbol, limit, 100 * cap_pct)
        self.ib.sleep(wait_s)

        if cancel_unfilled and trade.orderStatus.status != "Filled" and int(trade.orderStatus.filled) < qty:
            log.info("Cancelling unfilled remainder of %s BUY (%d/%d filled)",
                     symbol, int(trade.orderStatus.filled), qty)
            self.ib.cancelOrder(trade.order)
            # Cancels RACE fills ("too late to cancel" is routine). Wait for a
            # final state, then re-read - the pre-cancel snapshot may be stale,
            # and understating fills would leave shares without a stop.
            waited = 0.0
            while not trade.isDone() and waited < 5.0:
                self.ib.sleep(0.25)
                waited += 0.25

        filled = int(trade.orderStatus.filled)
        self.last_fill_price = float(trade.orderStatus.avgFillPrice or 0)
        log.info("ORDER STATUS: BUY %d %s -> %s (filled %d @ avg %s)",
                 qty, symbol, trade.orderStatus.status, filled,
                 trade.orderStatus.avgFillPrice)
        journal(
            symbol, "BUY", "entry_limit", qty=qty, filled=filled,
            signal_price=last_price, fill_price=float(trade.orderStatus.avgFillPrice or 0),
            note=f"[{self.sleeve}] status={trade.orderStatus.status} cap={cap_pct:.3%}",
        )
        return filled

    def wait_order_done(self, trade, timeout_s: float = 20.0) -> int:
        """Wait for a FINAL order state; return shares filled per the ORDER.

        The order's own fill report beats ib.positions(), which lags fills
        by seconds. Acting on that stale cache is exactly how the 2026-08-04
        closing flatten sold the same shares twice and went short: the sell
        filled, the cache still showed the old quantity, and the retry sold
        again. Callers that just sold something must judge success from THIS,
        never from an immediate position re-read."""
        if trade is None:
            return 0
        waited = 0.0
        while not trade.isDone() and waited < timeout_s:
            self.ib.sleep(0.5)
            waited += 0.5
        return int(trade.orderStatus.filled or 0)

    def settled_position_qty(self, symbol: str, expected_max: int,
                             timeout_s: float = 6.0) -> int:
        """Position size SAFE TO SELL after a cancel that may have raced fills.

        ib.positions() lags fills by seconds. Selling what it reports moments
        after a confirmed cancel re-sells already-filled shares - the exact
        2026-08-04 double-sell short. `expected_max` is what arithmetic says
        can possibly remain (pre-cancel quantity minus fills the cancel
        confirmed). Waits briefly for the cache to agree, then trusts the
        ARITHMETIC bound, never the cache."""
        expected_max = max(0, expected_max)
        waited = 0.0
        qty = self.position_qty(symbol)
        while qty > expected_max and waited < timeout_s:
            self.ib.sleep(0.5)
            waited += 0.5
            qty = self.position_qty(symbol)
        if qty > expected_max:
            log.error("%s: position cache says %d but only %d can remain after "
                      "confirmed fills - using the smaller (stale-cache guard)",
                      symbol, qty, expected_max)
            return expected_max
        return qty

    def place_protective_stop(self, symbol: str, qty: int, stop_price: float,
                              tif: str = "DAY"):
        """Resting stop-loss SELL held at IBKR's servers.

        NO LIVE CALLER since 2026-08-04: protective stops were removed by
        user directive ("no stop losses"). Kept intact so the capability can
        be reinstated at a monthly evaluation gate without rework.
        """
        from ib_async import Stock, StopOrder

        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        order = StopOrder("SELL", qty, round(stop_price, 2))
        order.tif = tif
        trade = self.ib.placeOrder(contract, order)
        log.info("PROTECTIVE STOP placed: SELL %d %s if price <= %.2f (%s)",
                 qty, symbol, round(stop_price, 2), tif)
        journal(symbol, "SELL", "stop_placed", qty=qty, signal_price=round(stop_price, 2),
                note=f"[{self.sleeve}] tif={tif}")
        return trade

    def submit_market_order(
        self,
        symbol: str,
        action: str,  # "BUY" or "SELL"
        qty: int,
        risk: RiskManager,
        last_price: float,
    ):
        """Place a market order, but only if the risk manager approves."""
        from ib_async import MarketOrder, Stock

        equity = self.net_liquidation()
        if action == "SELL" and symbol in self.pending_order_symbols(actions=("SELL",)):
            log.warning("SKIP SELL %s: a sell order is already working", symbol)
            return None

        if action == "BUY":
            allowed, reason = risk.can_open_position(equity, self.open_position_count())
            if not allowed:
                log.warning("BLOCKED BUY %s x%d: %s", symbol, qty, reason)
                return None
            ok, reason = risk.sanity_check_order(symbol, qty, last_price, equity)
            if not ok:
                log.warning("BLOCKED BUY %s x%d: %s", symbol, qty, reason)
                return None

        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        order = MarketOrder(action, qty)
        trade = self.ib.placeOrder(contract, order)
        log.info("ORDER SUBMITTED: %s %d %s (market)", action, qty, symbol)

        # Give IBKR a few seconds to fill; daily strategies don't need speed.
        self.ib.sleep(5)
        log.info(
            "ORDER STATUS: %s %d %s -> %s (filled %s @ avg %s)",
            action, qty, symbol,
            trade.orderStatus.status,
            trade.orderStatus.filled,
            trade.orderStatus.avgFillPrice,
        )
        journal(
            symbol, action, "market_order", qty=qty,
            filled=int(trade.orderStatus.filled),
            signal_price=last_price,
            fill_price=float(trade.orderStatus.avgFillPrice or 0),
            note=f"[{self.sleeve}] status={trade.orderStatus.status}",
        )
        return trade

    def place_moc_sell(self, symbol: str, qty: int, risk: RiskManager | None = None,
                       last_price: float = 0.0):
        """Sell via Market-on-Close: joins the closing auction instead of
        paying the spread at 3:55 - the evidence-preferred way to exit at
        end of day (single price, deep liquidity).

        VERIFIES acceptance. An MOC can be rejected (past the exchange cutoff,
        unsupported route), and an unnoticed rejection would strand the
        position with no exit and no stop - so on rejection we confirm the MOC
        is truly dead, then fall back to a market sell.
        """
        from ib_async import Order, Stock

        contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        order = Order(action="SELL", orderType="MOC", totalQuantity=qty)
        trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(3)
        status = trade.orderStatus.status
        log.info("MOC SELL %d %s -> %s", qty, symbol, status)
        journal(symbol, "SELL", "moc_sell", qty=qty, note=f"[{self.sleeve}] status={status}")

        if status in self.ACTIVE_STATUSES or status == "Filled":
            return trade

        # Rejected/Inactive: make sure it can never execute, then fall back.
        log.error("MOC REJECTED for %s (%s) - confirming dead, then market sell", symbol, status)
        waited = 0.0
        while not trade.isDone() and waited < 5.0:
            self.ib.sleep(0.25)
            waited += 0.25
        if not trade.isDone():
            log.critical("%s MOC neither accepted nor dead - NOT sending fallback "
                         "(double-sell risk). Resolve in TWS.", symbol)
            return trade
        qty_now = self.position_qty(symbol)
        if qty_now > 0 and risk is not None:
            self.submit_market_order(symbol, "SELL", qty_now, risk, last_price)
        return trade
