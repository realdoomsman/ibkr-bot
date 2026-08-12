"""Configuration loading with a hard guard against accidental live trading."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Ports that connect to REAL-MONEY accounts.
LIVE_PORTS = {7496, 4001}
LIVE_OPT_IN_ENV = "I_UNDERSTAND_LIVE_TRADING_RISKS"

# ---- Universe blocklists (user directives 2026-08-05) ----------------------
# "No index funds or anything like QQQ": index/leveraged/volatility ETFs and
# ETNs. Screens rank by volume and volatility, which leveraged ETFs dominate -
# without this list they would constantly win basket seats.
ETF_BLOCKLIST = {
    "SPY", "QQQ", "IWM", "DIA", "VOO", "IVV", "VTI", "RSP",
    "TQQQ", "SQQQ", "SPXL", "SPXU", "SPXS", "UPRO", "SSO", "SDS", "QLD", "QID",
    "SOXL", "SOXS", "SOXX", "SMH", "TSLL", "TSLS", "NVDL", "NVDD", "MSTU", "MSTZ",
    "TNA", "TZA", "LABU", "LABD", "FAS", "FAZ", "YINN", "YANG", "FNGU", "FNGD",
    "UVXY", "VXX", "UVIX", "SVXY", "SVIX", "VIXY",
    "IBIT", "FBTC", "GBTC", "ETHA", "ETHU", "BITX", "BITO", "ARKK", "ARKW",
    "XLF", "XLE", "XLK", "XLV", "XLU", "XLI", "XLP", "XLY", "XBI", "GDX", "GDXJ",
    "EEM", "EFA", "FXI", "KWEB", "EWZ", "TLT", "TMF", "TMV", "HYG", "LQD",
    "USO", "UNG", "BOIL", "KOLD", "SLV", "GLD", "UCO", "SCO",
}
# "No gambling stocks": casino, sports-betting, and iGaming operators.
GAMBLING_TICKERS = {
    "DKNG", "FLUT", "MGM", "LVS", "WYNN", "CZR", "PENN", "BALY", "RSI",
    "GENI", "CHDN", "BYD", "MCRI", "GDEN", "SRAD", "GAMB", "EVRI", "IGT",
    "LNW", "ACEL", "AGS", "SGHC", "BETR", "IMAX",  # IMAX excluded: often
    # miscategorized by screeners; harmless to skip one theater chain.
}
# Names the user has banned by direct instruction, enforced fleet-wide
# (radar capture, mover sleeve, rotation baskets, watchdog banned-held).
USER_BLOCKLIST = {
    "PLTR", "PLNTR",  # user directive 2026-08-12: "Dont buy PLNTR stock"
                      # (PLTR = Palantir; both spellings blocked, zero cost)
}
BLOCKED_SYMBOLS = ETF_BLOCKLIST | GAMBLING_TICKERS | USER_BLOCKLIST


@dataclass
class IBConfig:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 42
    #: 3 = delayed (~15 min, free). 1 = REAL-TIME - requires the US Securities
    #: Snapshot and Futures Value Bundle subscription AND paper-account data
    #: sharing enabled in Client Portal. Flip to 1 only after both are active,
    #: or market-data requests fail instead of silently degrading.
    market_data_type: int = 3


@dataclass
class StrategyConfig:
    name: str = "sma_cross"
    symbols: list[str] = field(default_factory=lambda: ["SPY"])
    history_days: int = 400
    params: dict = field(default_factory=dict)


@dataclass
class RiskConfig:
    max_position_pct: float = 0.10
    max_open_positions: int = 5
    max_daily_loss_pct: float = 0.02
    #: Daily profit lockout (user directive 2026-08-05): once the day's gain
    #: reaches this fraction of effective capital, no NEW entries for the
    #: rest of the day. Exits and end-of-day flattens still run. 0 = off.
    max_daily_gain_pct: float = 0.10
    #: Working capital cap. The bot sizes positions and loss limits off
    #: min(account equity, capital_base) so a big paper account can practice
    #: at realistic real-money scale. 0 = use full account equity.
    capital_base: float = 0.0


@dataclass
class IntradayConfig:
    enabled: bool = False
    symbols: list[str] = field(default_factory=lambda: ["QQQ"])
    strategy: str = "sma_cross"
    params: dict = field(default_factory=dict)
    bar_size: str = "5 mins"
    scan_minutes: int = 5
    flatten_at_close: bool = True
    max_trades_per_day: int = 20
    # ATR-scaled stop PLACEMENT (evidence: flat %-stops sit inside routine
    # noise in high-vol names and become pure drag - Kaminski & Lo). Position
    # SIZE is unchanged - full max_position_pct of capital.
    atr_stop_mult: float = 2.0      # stop = entry - 2 x ATR(14) of 5-min bars
    stop_loss_pct: float = 0.03     # fallback when ATR is unavailable
    client_id: int = 43  # separate API client from the daily bot


@dataclass
class BotConfig:
    mode: str = "paper"
    ib: IBConfig = field(default_factory=IBConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    intraday: IntradayConfig = field(default_factory=IntradayConfig)


def load_config(path: str | Path = "config.yaml") -> BotConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    cfg = BotConfig(
        mode=raw.get("mode", "paper"),
        ib=IBConfig(**raw.get("ib", {})),
        strategy=StrategyConfig(**raw.get("strategy", {})),
        risk=RiskConfig(**raw.get("risk", {})),
        intraday=IntradayConfig(**raw.get("intraday", {})),
    )
    _enforce_paper_guard(cfg)
    return cfg


def _enforce_paper_guard(cfg: BotConfig) -> None:
    """Refuse to run against a live account unless explicitly opted in.

    Two independent checks must BOTH say "live" before real money is at risk:
    the port number and the explicit environment variable. This is deliberate
    friction - a typo in config.yaml should never cost you money.
    """
    wants_live = cfg.mode.lower() == "live" or cfg.ib.port in LIVE_PORTS
    if not wants_live:
        return
    if os.environ.get(LIVE_OPT_IN_ENV, "").lower() != "yes":
        raise SystemExit(
            f"REFUSING TO START: config points at a LIVE trading port/mode "
            f"(mode={cfg.mode!r}, port={cfg.ib.port}).\n"
            f"This bot is an educational project. If you truly want live "
            f"trading, set the environment variable {LIVE_OPT_IN_ENV}=yes.\n"
            f"For paper trading, use port 7497 (TWS) or 4002 (IB Gateway) "
            f"and mode: paper."
        )
