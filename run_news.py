"""Continuous news watcher for the bot's symbols.

Polls Yahoo Finance headlines for every symbol the bots trade (plus any
extras you pass), remembers what it has already seen, and logs only NEW
headlines to the console and news.log. If ANTHROPIC_API_KEY is set, each
batch of fresh headlines also gets a structured AI read (sentiment +
risks) - advisory only, and only called when there is actually news, so
API costs stay proportional to news flow.

Why this never trades: by the time a headline reaches a free feed, HFT
firms repriced the stock milliseconds after the wire. A retail bot
reacting to public news is always last in line. Use this to KNOW what is
moving your names - not to chase it.

Run:  python run_news.py            # loop 8:00-20:00, poll every 10 min
      python run_news.py --once     # single pass
      python run_news.py --symbols AMC EOSE --interval 5
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import time
from pathlib import Path

from bot import ai_analyst
from bot.config import load_config
from bot.data import fetch_headlines, fetch_yahoo_daily

log = logging.getLogger("bot.news")

STATE_FILE = Path("news_seen.json")
DAY_END_HOUR = 20  # stop at 8 PM local; Task Scheduler relaunches next morning
MAX_REMEMBERED = 300


def load_seen() -> dict[str, list[str]]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.warning("news_seen.json unreadable - starting fresh")
    return {}


def save_seen(seen: dict[str, list[str]]) -> None:
    trimmed = {s: hashes[-MAX_REMEMBERED:] for s, hashes in seen.items()}
    STATE_FILE.write_text(json.dumps(trimmed, indent=0), encoding="utf-8")


def headline_id(title: str) -> str:
    return hashlib.sha256(title.strip().lower().encode()).hexdigest()[:16]


def scan_once(watchlist: list[str], seen: dict[str, list[str]]) -> int:
    total_new = 0
    for symbol in watchlist:
        titles = fetch_headlines(symbol, limit=15)
        known = set(seen.setdefault(symbol, []))
        fresh = [t for t in titles if headline_id(t) not in known]
        if not fresh:
            continue
        total_new += len(fresh)
        for t in fresh:
            log.info("NEWS %s: %s", symbol, t)
            seen[symbol].append(headline_id(t))

        # Optional AI read - only on symbols with genuinely new headlines.
        if ai_analyst.is_enabled():
            try:
                bars = fetch_yahoo_daily(symbol, years=1)
                analysis = ai_analyst.analyze_symbol(symbol, bars, headlines=fresh)
                if analysis:
                    log.info(
                        "AI READ %s: %s (conf %.2f) %s",
                        symbol, analysis.sentiment, analysis.confidence, analysis.summary,
                    )
            except Exception:
                log.warning("AI read failed for %s - headlines still logged", symbol)
    return total_new


def main() -> None:
    parser = argparse.ArgumentParser(description="Continuous news watcher")
    parser.add_argument("--symbols", nargs="*", default=[], help="Extra symbols to watch")
    parser.add_argument("--interval", type=int, default=10, help="Poll interval (minutes)")
    parser.add_argument("--once", action="store_true", help="Single pass, then exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("news.log", encoding="utf-8")],
    )

    cfg = load_config()
    extra = []
    extra_file = Path("news_extra.txt")  # fed by the Penny Radar
    if extra_file.exists():
        extra = [s.strip().upper() for s in extra_file.read_text(encoding="utf-8").split() if s.strip()]
    watchlist = list(dict.fromkeys(
        cfg.strategy.symbols + cfg.intraday.symbols
        + [s.upper() for s in args.symbols] + extra
    ))
    ai_state = "ON" if ai_analyst.is_enabled() else "off (set ANTHROPIC_API_KEY to enable)"
    log.info("News watcher up. Watching %s | poll every %dmin | AI reads: %s",
             watchlist, args.interval, ai_state)

    seen = load_seen()
    while True:
        new_count = scan_once(watchlist, seen)
        save_seen(seen)
        if new_count == 0:
            log.info("No new headlines this pass.")
        if args.once:
            break
        if dt.datetime.now().hour >= DAY_END_HOUR:
            log.info("End of news day - exiting (Task Scheduler restarts me tomorrow).")
            break
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    main()
