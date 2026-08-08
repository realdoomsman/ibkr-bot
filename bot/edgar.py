"""Free SEC EDGAR dilution red-flags for the penny tier.

Research-backed (FINRA 22-25; DilutionWatch 2025-26; Kim/Klein/Rosenfeld
2008): the dominant collapse mechanism for spiking sub-$5 names is company
paperwork being sold INTO the spike - 424B3 (active ATM drip), 424B5/424B4
(shelf takedowns priced below market), S-1/S-3 registrations going EFFECTive
(restricted holders free to sell). These filings hit EDGAR in real time -
one of the few signals this fleet gets that is NOT 15 minutes stale.

Free endpoints, cached on disk, throttle-friendly, never raises:
  https://www.sec.gov/files/company_tickers.json          ticker -> CIK
  https://data.sec.gov/submissions/CIK##########.json     recent filings
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("bot.edgar")

UA = {"User-Agent": "ibkr-paper-research-bot contact realdoomsalt@gmail.com"}
CACHE = Path("data_cache/edgar")
DILUTION_FORMS = ("424B3", "424B4", "424B5", "S-1", "S-3", "EFFECT")


def _get_json(url: str, cache_name: str, max_age_h: float) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / cache_name
    if p.exists() and (time.time() - p.stat().st_mtime) < max_age_h * 3600:
        return json.loads(p.read_text(encoding="utf-8"))
    req = urllib.request.Request(url, headers=UA)
    data = urllib.request.urlopen(req, timeout=15).read()
    p.write_bytes(data)
    return json.loads(data)


def cik_for(symbol: str) -> str | None:
    try:
        m = _get_json("https://www.sec.gov/files/company_tickers.json",
                      "tickers.json", max_age_h=40)
        sym = symbol.upper()
        for v in m.values():
            if str(v.get("ticker", "")).upper() == sym:
                return f"{int(v['cik_str']):010d}"
    except Exception:
        log.debug("CIK lookup failed for %s", symbol, exc_info=True)
    return None


# EDGAR stateOrCountry codes: F4 = China, K3 = Hong Kong. (Cayman/BVI shells
# with a China business address still show F4/K3 in the BUSINESS address.)
CHINA_CODES = {"F4", "K3"}


def is_china_based(symbol: str) -> bool:
    """True if the company's EDGAR business address or state of incorporation
    is China/Hong Kong (user directive 2026-08-05: don't trade Chinese
    companies, including their US/Nasdaq listings).

    False when US/other or UNKNOWN - missing data never blocks the world,
    so this is a best-effort screen, not a guarantee. Never raises."""
    try:
        cik = cik_for(symbol)
        if not cik:
            return False
        sub = _get_json(f"https://data.sec.gov/submissions/CIK{cik}.json",
                        f"sub_{cik}.json", max_age_h=18)
        biz = sub.get("addresses", {}).get("business", {}) or {}
        # Foreign filers scatter the country across several fields, and
        # China-operating shells often incorporate in BVI/Cayman - so check
        # every field EDGAR offers, not just stateOrCountry.
        codes = {str(biz.get(k) or "").upper()
                 for k in ("stateOrCountry", "countryCode")}
        country = str(biz.get("country") or "").upper()
        inc = str(sub.get("stateOfIncorporation") or "").upper()
        return bool(codes & CHINA_CODES) or country in {"CHINA", "HONG KONG"} \
            or inc in CHINA_CODES
    except Exception:
        log.debug("china check failed for %s", symbol, exc_info=True)
        return False


def dilution_flags(symbol: str, days: int = 90) -> str:
    """Recent dilution-machinery filings, e.g. '424B5:2026-07-30|S-3:2026-06-11'.

    Empty string = nothing on record OR EDGAR unreachable (best effort - a
    missing flag is never proof of cleanliness). Never raises.
    """
    try:
        cik = cik_for(symbol)
        if not cik:
            return ""
        sub = _get_json(f"https://data.sec.gov/submissions/CIK{cik}.json",
                        f"sub_{cik}.json", max_age_h=18)
        recent = sub.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        flags = [
            f"{f}:{d}" for f, d in zip(forms, dates)
            if d >= cutoff and any(str(f).startswith(w) for w in DILUTION_FORMS)
        ]
        return "|".join(flags[:6])
    except Exception:
        log.debug("dilution check failed for %s", symbol, exc_info=True)
        return ""
