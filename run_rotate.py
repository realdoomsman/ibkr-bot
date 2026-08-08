"""Pre-market basket rotation: the universe is dynamic, not a fixed list.

Every morning before anything else starts, this rescans the market and
refreshes both trading baskets using the SAME mechanical rules as always
(no discretion, no cherry-picking):

  intraday basket: top 3 by dollar volume, price <= $5, $-vol >= $50M/day
  daily basket:    top 3 by annualized volatility, price <= $20, $-vol >= $100M/day

Safety rules that make dynamic rotation sane:
  - STICKY-WHILE-HELD: a symbol the account currently holds NEVER rotates
    out of its basket - otherwise the position would be orphaned with no
    bot managing its exit.
  - Baskets never overlap each other or the ETF sleeves (SPY/QQQ).
  - Any failure (screener down, TWS unreachable) leaves config.yaml
    untouched - yesterday's baskets are always a safe fallback.

Runs 7:45 AM weekdays (before the fleet wakes), or manually:
    python run_rotate.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path

log = logging.getLogger("bot.rotate")

CONFIG = Path("config.yaml")
RESERVED = {"SPY", "QQQ"}  # the ETF sleeves' instruments - never in baskets
BASKET_SIZE = 4  # aggressive profile: four symbols per basket
INTRA_RULE = dict(max_price=5.0, min_dvol=50.0, rank="dollar_vol_m")
DAILY_RULE = dict(max_price=20.0, min_dvol=100.0, rank="ann_vol")
CLIENT_ID = 48


def held_symbols() -> set[str] | None:
    """Symbols with nonzero positions. None = couldn't check (abort rotation)."""
    try:
        from ib_async import IB

        ib = IB()
        ib.connect("127.0.0.1", 7497, clientId=CLIENT_ID, timeout=15)
        try:
            return {p.contract.symbol for p in ib.positions() if p.position != 0}
        finally:
            ib.disconnect()
    except Exception as e:
        log.warning("Cannot verify held positions (%s) - rotation aborted", e)
        return None


def current_baskets() -> tuple[list[str], list[str]]:
    from bot.config import load_config

    cfg = load_config()
    return list(cfg.strategy.symbols), list(cfg.intraday.symbols)


def build_basket(old, held, candidates, used: set[str]) -> list[str]:
    """Keep held symbols (sticky), fill the rest from ranked candidates."""
    basket = [s for s in old if s in held and s not in used]
    used.update(basket)
    for row in candidates:
        if len(basket) >= BASKET_SIZE:
            break
        sym = row["symbol"]
        if sym in used or sym in RESERVED:
            continue
        basket.append(sym)
        used.add(sym)
    return basket


def rewrite_config(daily: list[str], intraday: list[str]) -> None:
    """Replace only the two symbols: lines, preserving all comments."""
    lines = CONFIG.read_text(encoding="utf-8").splitlines()
    section = None
    out = []
    for line in lines:
        if re.match(r"^[A-Za-z_]+:", line):
            section = line.split(":")[0]
        if section == "strategy" and re.match(r"^\s+symbols:", line):
            line = f"  symbols: [{', '.join(daily)}]"
        elif section == "intraday" and re.match(r"^\s+symbols:", line):
            line = f"  symbols: [{', '.join(intraday)}]"
        out.append(line)
    CONFIG.write_text("\n".join(out) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-market basket rotation")
    parser.add_argument("--dry-run", action="store_true", help="Show the plan, change nothing")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("rotation.log", encoding="utf-8")],
    )

    # Refresh the exchange toxicity feeds first (FSI distress codes, exact
    # ETF flags, reverse-split announcements) - the whole fleet reads the
    # resulting toxicity.json all day.
    tox = None
    try:
        from bot.toxicity import block_reason as tox_block
        from bot.toxicity import refresh as tox_refresh

        tox = tox_refresh()
    except Exception:
        log.warning("toxicity refresh failed - continuing with yesterday's file",
                    exc_info=True)

    daily_old, intra_old = current_baskets()
    held = held_symbols()
    if held is None:
        return  # keep yesterday's baskets - never rotate blind
    log.info("Currently held: %s", sorted(held) or "nothing")

    from run_screener import candidate_symbols, score_symbol

    try:
        pool = candidate_symbols(max_price=max(INTRA_RULE["max_price"], DAILY_RULE["max_price"]))
    except Exception as e:
        log.warning("Screener unavailable (%s) - keeping current baskets", e)
        return
    # Never pull ANOTHER sleeve's held symbol into a basket: two engines
    # managing one account-wide position would sell each other's shares
    # (e.g. the intraday flatten force-selling the penny sleeve's moon bag).
    foreign_held = held - set(daily_old) - set(intra_old)
    if foreign_held:
        log.info("Excluding other sleeves' held symbols: %s", sorted(foreign_held))

    from bot.config import BLOCKED_SYMBOLS
    from bot.edgar import is_china_based

    scored = []
    for sym in pool[:60]:
        if sym.upper() in BLOCKED_SYMBOLS:
            continue  # no ETFs, no gambling stocks (user directives 2026-08-05)
        if tox is not None:
            try:
                why = tox_block(sym.upper(), tox)
                if why:
                    log.info("Skipping %s: %s", sym, why)
                    continue
            except Exception:
                pass
        if is_china_based(sym):
            log.info("Skipping %s: China/HK-based (user directive 2026-08-05)", sym)
            continue
        row = score_symbol(sym)
        if row and row["ann_vol"] and row["symbol"] not in RESERVED \
                and row["symbol"] not in foreign_held:
            scored.append(row)
    if len(scored) < 6:
        log.warning("Only %d scored candidates - keeping current baskets", len(scored))
        return

    # Point-in-time archive: today's scored screen, kept forever, so future
    # filter retrains can build a NON-survivorship-biased universe from what
    # was actually visible each day (review finding).
    try:
        archive = Path("screens_archive")
        archive.mkdir(exist_ok=True)
        import pandas as _pd

        _pd.DataFrame(scored).to_csv(
            archive / f"screen_{__import__('datetime').date.today().isoformat()}.csv",
            index=False,
        )
    except Exception:
        log.debug("screen archive failed", exc_info=True)

    intra_cands = sorted(
        (r for r in scored
         if r["price"] <= INTRA_RULE["max_price"] and r["dollar_vol_m"] >= INTRA_RULE["min_dvol"]),
        key=lambda r: -r[INTRA_RULE["rank"]],
    )
    daily_cands = sorted(
        (r for r in scored
         if r["price"] <= DAILY_RULE["max_price"] and r["dollar_vol_m"] >= DAILY_RULE["min_dvol"]),
        key=lambda r: -r[DAILY_RULE["rank"]],
    )

    used: set[str] = set()
    intra_new = build_basket(intra_old, held, intra_cands, used)
    daily_new = build_basket(daily_old, held, daily_cands, used)

    log.info("Intraday basket: %s -> %s%s", intra_old, intra_new,
             "  (unchanged)" if intra_new == intra_old else "")
    log.info("Daily basket:    %s -> %s%s", daily_old, daily_new,
             "  (unchanged)" if daily_new == daily_old else "")

    if args.dry_run:
        log.info("Dry run - config.yaml untouched")
        return
    if intra_new == intra_old and daily_new == daily_old:
        log.info("No rotation needed today")
        return
    if len(intra_new) < BASKET_SIZE or len(daily_new) < BASKET_SIZE:
        log.warning("Could not fill both baskets fully - keeping current baskets")
        return

    rewrite_config(daily_new, intra_new)
    log.info("config.yaml updated - the fleet picks up the new baskets as it starts today")


if __name__ == "__main__":
    main()
