"""Fleet watchdog: the bot that watches the bots - and tells YOUR PHONE.

Born from the 2026-08-05..07 incident: TWS died Tuesday night and the whole
fleet silently slept for three trading days while shorts sat in the account.
Nobody knew, because nothing was watching the watchers. Now this is.

Every run it checks:
  1. TWS reachable?             -> CRITICAL alert if not (the #1 failure)
  2. Engines alive?             -> heartbeat files must be fresh during RTH
  3. Any SHORT positions?       -> alert + AUTO-COVER during market hours,
                                   but only after a persistence re-check so
                                   it never doubles an engine's own repair
  4. Banned symbols held?       -> ETFs / gambling / blocklist (directives)
  5. Unmanaged longs?           -> positions no engine is responsible for
  6. Positions after the close? -> the EOD flatten must leave cash only
  7. VM clock actually Eastern? -> tasks fire at wall-clock times
  8. Sunday evening special     -> IBKR kills sessions early Sunday; if TWS
                                   isn't logged in, Monday is a blackout day.

Alerting rules (review-hardened): a problem pings the phone at most every
2 hours; the dedupe stamp is written only AFTER a successful push (a failed
push retries next run); when a problem RESOLVES its stamp is cleared, so a
recurrence alerts immediately. Status lands in health.json for the
dashboard banner.

Schedule: "IBKR Watchdog" - every 30 min through 16:20 on weekdays + Sunday
20:00, registered with S4U logon so it runs even when no RDP session exists
(it must survive exactly the failures it watches for).
Manual run:  python run_watchdog.py
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger("bot.watchdog")

NY = ZoneInfo("America/New_York")
HEALTH = Path("health.json")
STATE = Path("watchdog_state.json")
ALERTS_CFG = Path("alerts.yaml")
HEARTBEATS = {"intraday engine": Path("heartbeat_intraday.txt"),
              "penny sleeve": Path("heartbeat_penny.txt")}
REALERT_HOURS = 2
CLIENT_ID = 90


def _ntfy_topic() -> str:
    try:
        import yaml

        cfg = yaml.safe_load(ALERTS_CFG.read_text(encoding="utf-8")) or {}
        return str(cfg.get("ntfy_topic") or "").strip()
    except Exception:
        return ""


class Alerter:
    """Dedupe with recovery-reset: stamps survive in watchdog_state.json."""

    def __init__(self):
        try:
            self.state = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            self.state = {}
        self.fired: set[str] = set()
        self.topic = _ntfy_topic()

    def alert(self, key: str, title: str, message: str) -> None:
        self.fired.add(key)
        log.warning("ALERT [%s]: %s - %s", key, title, message)
        last = self.state.get(key)
        if last:
            try:
                if (dt.datetime.now() - dt.datetime.fromisoformat(last)) \
                        .total_seconds() < REALERT_HOURS * 3600:
                    return  # pinged recently - don't spam the phone
            except ValueError:
                pass
        pushed = False
        if self.topic:
            try:
                req = urllib.request.Request(
                    f"https://ntfy.sh/{self.topic}",
                    data=message.encode("utf-8"),
                    headers={"Title": title, "Priority": "high",
                             "Tags": "rotating_light"},
                )
                urllib.request.urlopen(req, timeout=10)
                pushed = True
            except Exception:
                log.debug("ntfy push failed - will retry next run", exc_info=True)
        # Stamp only after a SUCCESSFUL push (or when push is disabled):
        # a failed push must retry next run, not go silent for 2 hours.
        if pushed or not self.topic:
            self.state[key] = dt.datetime.now().isoformat(timespec="seconds")

    def finish(self) -> None:
        # Recovery reset: keys that did NOT fire this run are resolved -
        # clear their stamps so a recurrence alerts immediately.
        for k in list(self.state):
            if k not in self.fired:
                self.state.pop(k)
        STATE.write_text(json.dumps(self.state, indent=0), encoding="utf-8")


def rth_now(now: dt.datetime) -> bool:
    return now.weekday() < 5 and dt.time(9, 30) <= now.time() < dt.time(16, 0)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("watchdog.log", encoding="utf-8")],
    )
    logging.getLogger("ib_async").setLevel(logging.ERROR)

    now = dt.datetime.now(NY)
    al = Alerter()
    problems: list[str] = []
    health: dict = {"checked": now.isoformat(timespec="seconds"), "tws_ok": False,
                    "positions": [], "shorts": [], "problems": problems}
    guard_tripped = False

    # ---- 7. VM clock sanity: tasks fire at LOCAL times ---------------------
    if dt.datetime.now().astimezone().utcoffset() != now.utcoffset():
        problems.append("VM clock is NOT Eastern time - every schedule is shifted")
        al.alert("tz", "IBKR fleet: wrong timezone",
                 'The VM clock is not US Eastern. Every bot fires at the wrong '
                 'market time. Fix: tzutil /s "Eastern Standard Time" and reboot.')

    # ---- 1. TWS reachable? -------------------------------------------------
    broker = None
    try:
        from bot.broker import Broker
        from bot.config import load_config

        cfg = load_config()
        cfg.ib.client_id = CLIENT_ID
        broker = Broker(cfg, sleeve="watchdog")
        broker.connect()
        health["tws_ok"] = True
        health["equity"] = round(broker.net_liquidation(), 2)
    except SystemExit as e:
        # Paper guard: TWS is logged into something that is NOT the paper
        # account. This is the LOUDEST possible alarm, never a silent death.
        guard_tripped = True
        problems.append("PAPER GUARD TRIPPED - TWS on a non-paper account")
        al.alert("paper_guard", "IBKR fleet: WRONG ACCOUNT IN TWS",
                 f"The watchdog refused to touch the connected account: {e}. "
                 f"Check which account TWS is logged into RIGHT NOW.")
    except Exception as e:
        problems.append("TWS UNREACHABLE")
        msg = (f"TWS is DOWN on the VM ({type(e).__name__}). The fleet cannot "
               f"trade or repair anything until someone logs it back in.")
        if now.weekday() == 6:
            msg += " It is SUNDAY: log into TWS TONIGHT or Monday is a lost day."
        al.alert("tws_down", "IBKR fleet: TWS DOWN", msg)

    if broker is not None and health["tws_ok"]:
        try:
            from bot.config import BLOCKED_SYMBOLS

            def positions_now():
                return [(p.contract.symbol, int(p.position))
                        for p in broker.ib.positions() if p.position != 0]

            positions = positions_now()
            health["positions"] = positions

            # ---- 3. shorts: persistence-checked auto-cover in RTH ---------
            shorts = [(s, q) for s, q in positions if q < 0]
            health["shorts"] = shorts
            if shorts:
                key = "shorts:" + ",".join(sorted(s for s, _ in shorts))
                problems.append(f"SHORT: {shorts}")
                al.alert(key, "IBKR fleet: SHORT POSITION",
                         f"Account is short {shorts}. "
                         + ("Auto-covering after a persistence check."
                            if rth_now(now) else
                            "Will need covering when the market opens."))
                if rth_now(now):
                    # An engine may be covering RIGHT NOW; a cover that just
                    # filled vanishes from open orders while the position
                    # cache still shows the short. Re-check after a pause so
                    # we never stack a second cover into an unmanaged LONG.
                    broker.ib.sleep(4)
                    still_short = {s: q for s, q in positions_now() if q < 0}
                    for sym, q in shorts:
                        if sym in still_short \
                                and sym not in broker.pending_order_symbols(actions=("BUY",)):
                            broker.buy_to_cover(sym, -still_short[sym])

            # ---- 4. banned names held -------------------------------------
            banned = [s for s, _ in positions if s in BLOCKED_SYMBOLS]
            if banned:
                problems.append(f"banned held: {banned}")
                al.alert("banned", "IBKR fleet: banned symbol held",
                         f"Account holds {banned} - blocked by your directives "
                         f"(ETF/gambling list). The engines should exit them in RTH.")

            # ---- 5. unmanaged longs: nobody is responsible for these ------
            managed = set(cfg.strategy.symbols) | set(cfg.intraday.symbols)
            try:
                pstate = json.loads(Path("pennymo_state.json").read_text(encoding="utf-8"))
                managed |= set((pstate.get("positions") or {}).keys())
            except Exception:
                pass
            orphans = [s for s, q in positions if q > 0
                       and s not in managed and s not in ("QQQ", "SPY")]
            if orphans:
                problems.append(f"unmanaged positions: {orphans}")
                al.alert("orphans", "IBKR fleet: unmanaged position",
                         f"Account holds {orphans} but NO engine manages them "
                         f"(not in any basket or ladder). They will sit there "
                         f"until handled - run flatten_now.py in market hours "
                         f"or sell manually.")

            # ---- 5b. held symbol HALTED? exits can't fill until resume ----
            try:
                from bot.halts import todays_halts

                halted_held = [s for s, _ in positions
                               if (h := todays_halts().get(s)) and not h["resumed"]]
                if halted_held:
                    problems.append(f"HALTED while held: {halted_held}")
                    al.alert("halt_held", "IBKR fleet: held symbol HALTED",
                             f"{halted_held} halted mid-hold - no exit can fill "
                             f"until it resumes; if near the close this can force "
                             f"an overnight hold.")
            except Exception:
                pass

            # ---- 6. anything held after the close? ------------------------
            if now.weekday() < 5 and now.time() >= dt.time(16, 5) and positions:
                problems.append("positions past the close")
                al.alert("overnight", "IBKR fleet: positions held overnight",
                         f"EOD flatten should have left cash only, but the "
                         f"account still holds {positions}.")
        except Exception:
            log.exception("account checks failed")
            problems.append("account checks failed")
        finally:
            broker.disconnect()

    # ---- 2. engine heartbeats (only meaningful during market hours) -------
    hb_status = {}
    for name, path in HEARTBEATS.items():
        age_min = None
        if path.exists():
            age_min = round((dt.datetime.now()
                             - dt.datetime.fromtimestamp(path.stat().st_mtime)).total_seconds() / 60)
        hb_status[name] = age_min
        if rth_now(now) and now.time() >= dt.time(9, 45) and (age_min is None or age_min > 15):
            problems.append(f"{name} not scanning")
            al.alert(f"hb_{name}", f"IBKR fleet: {name} not running",
                     f"No heartbeat from the {name} for "
                     f"{'ever' if age_min is None else f'{age_min} min'} during "
                     f"market hours. Its task may have crashed - check the VM.")
    health["heartbeats"] = hb_status

    if now.weekday() == 6 and health["tws_ok"]:
        log.info("Sunday check: TWS is logged in - Monday is a go.")

    al.finish()
    HEALTH.write_text(json.dumps(health, indent=2), encoding="utf-8")
    if problems:
        log.warning("Watchdog verdict: %d problem(s): %s", len(problems), "; ".join(problems))
    else:
        log.info("Watchdog verdict: all systems healthy.")
    if guard_tripped:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
