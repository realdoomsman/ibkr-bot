"""Optional AI market analysis using the Claude API.

This module is ADVISORY ONLY. It produces a structured second opinion
(sentiment + risks) that you can log next to your strategy's signals and
review by eye. It does not place trades, and it should never be the sole
reason a trade happens: language models are not price oracles, and past
commentary predicts nothing.

Requires: pip install anthropic, and ANTHROPIC_API_KEY set in the
environment. Every call costs real money (API tokens). If the key is not
set, analyze_symbol() quietly returns None and the bot works normally.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

import pandas as pd
from pydantic import BaseModel

log = logging.getLogger("bot.ai")

MODEL = "claude-opus-5"


class MarketAnalysis(BaseModel):
    sentiment: Literal["bullish", "bearish", "neutral"]
    confidence: float  # 0.0 (guess) to 1.0 (high conviction)
    summary: str
    key_risks: list[str]


def is_enabled() -> bool:
    """Key from the environment, else gitignored anthropic_key.txt beside
    the code (S4U scheduled tasks don't load user-level env vars - same
    fallback the Polygon key needed). Loading into os.environ lets the
    anthropic SDK pick it up without the value passing through any code."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    try:
        from pathlib import Path

        k = Path(__file__).resolve().parent.parent.joinpath(
            "anthropic_key.txt").read_text(encoding="utf-8-sig").strip()
        if k:
            os.environ["ANTHROPIC_API_KEY"] = k
            return True
    except Exception:
        pass
    return False


def _describe_recent_action(bars: pd.DataFrame) -> str:
    """Compress recent price action into a few numbers for the prompt."""
    close = bars["close"]
    last = close.iloc[-1]
    ret_5 = 100 * (last / close.iloc[-6] - 1) if len(close) > 6 else 0.0
    ret_21 = 100 * (last / close.iloc[-22] - 1) if len(close) > 22 else 0.0
    ret_63 = 100 * (last / close.iloc[-64] - 1) if len(close) > 64 else 0.0
    vol_21 = 100 * close.pct_change().tail(21).std() * (252 ** 0.5)
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else float("nan")
    return (
        f"last close {last:.2f}; "
        f"returns: 1w {ret_5:+.1f}%, 1m {ret_21:+.1f}%, 3m {ret_63:+.1f}%; "
        f"annualized 1m volatility {vol_21:.0f}%; "
        f"20d SMA {sma20:.2f}, 50d SMA {sma50:.2f}"
    )


def analyze_symbol(
    symbol: str,
    bars: pd.DataFrame,
    headlines: list[str] | None = None,
) -> MarketAnalysis | None:
    """Ask Claude for a structured read on a symbol. Returns None if disabled."""
    if not is_enabled():
        return None

    import anthropic

    client = anthropic.Anthropic()
    stats = _describe_recent_action(bars)
    headline_block = ""
    if headlines:
        joined = "\n".join(f"- {h}" for h in headlines[:20])
        headline_block = f"\n\nRecent headlines:\n{joined}"

    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=16000,
            system=(
                "You are a cautious market analyst producing a structured second "
                "opinion for a hobbyist's paper-trading bot. Be calibrated: use "
                "'neutral' freely, keep confidence low unless the evidence is "
                "strong, and never imply certainty about future prices."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Give a structured read on {symbol}.\n"
                        f"Recent price action: {stats}{headline_block}\n\n"
                        "Set confidence between 0.0 and 1.0."
                    ),
                }
            ],
            output_format=MarketAnalysis,
        )
    except anthropic.RateLimitError:
        log.warning("Claude API rate limited; skipping AI analysis for %s", symbol)
        return None
    except anthropic.APIStatusError as e:
        log.warning("Claude API error for %s: %s %s", symbol, e.status_code, e.message)
        return None
    except anthropic.APIConnectionError:
        log.warning("Could not reach Claude API; skipping AI analysis for %s", symbol)
        return None
    except Exception:
        # e.g. pydantic ValidationError if the structured output was truncated.
        # Advisory-only means NO failure here may ever block trading logic.
        log.warning("AI analysis failed for %s; skipping", symbol, exc_info=True)
        return None

    analysis = response.parsed_output
    if analysis is not None:
        log.info(
            "AI read on %s: %s (confidence %.2f) - %s",
            symbol, analysis.sentiment, analysis.confidence, analysis.summary,
        )
    return analysis
