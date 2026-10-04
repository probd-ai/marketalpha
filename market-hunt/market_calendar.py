"""NSE equity trading calendar: weekends plus the exchange's published trading holidays.

Source: NSE holiday-master (`/api/holiday-master?type=trading`, segment `CM` = capital market / equities), fetched by
the `calendar` stage of scripts/refresh_all.py and stored in Mongo `market_holidays` (one doc per date). Readers
use `load_holidays(db)`; with no calendar loaded every weekday counts as a session, which is the old behaviour.
"""
from __future__ import annotations

import datetime as dt
import logging

import numpy as np

logger = logging.getLogger(__name__)

HOLIDAY_URL = "https://www.nseindia.com/api/holiday-master?type=trading"
SEGMENT = "CM"
COLLECTION = "market_holidays"


# --------------------------------------------------------------------------------------------- source
async def fetch_holiday_master() -> dict:
    """Raw holiday-master payload. Raises on any HTTP/parse failure — the caller decides how to degrade."""
    import aiohttp

    from nse_data_client import _CA_HEADERS
    headers = {**_CA_HEADERS, "Accept-Encoding": "gzip, deflate"}   # NSE serves brotli otherwise; aiohttp can't decode it
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession() as session:
        async with session.get("https://www.nseindia.com", headers=headers, timeout=timeout):
            pass                                                     # NSE refuses API calls without its cookies
        async with session.get(HOLIDAY_URL, headers=headers, timeout=timeout) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)


def parse_holidays(payload) -> list[dict]:
    """[{date, description}] for the equity segment, sorted. ValueError if the payload has no usable rows."""
    rows = (payload or {}).get(SEGMENT) if isinstance(payload, dict) else None
    out = []
    for r in rows or []:
        try:
            d = dt.datetime.strptime(str(r["tradingDate"]).strip(), "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        out.append({"date": d, "description": str(r.get("description", "")).strip()})
    if not out:
        raise ValueError(f"holiday-master payload has no usable {SEGMENT} rows")
    return sorted(out, key=lambda r: r["date"])


# --------------------------------------------------------------------------------------------- storage
def store_holidays(db, rows: list[dict]) -> int:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    for r in rows:
        key = r["date"].isoformat()
        db[COLLECTION].replace_one({"_id": key}, {"_id": key, "description": r["description"], "segment": SEGMENT,
                                                  "source": HOLIDAY_URL, "fetched_at": now}, upsert=True)
    return len(rows)


def load_holidays(db) -> set[dt.date]:
    return {dt.date.fromisoformat(d["_id"]) for d in db[COLLECTION].find({}, {"_id": 1})}


def years_covered(db) -> set[int]:
    return {d.year for d in load_holidays(db)}


# --------------------------------------------------------------------------------------------- sessions
def _hol(holidays) -> list[str]:
    return [h.isoformat() for h in holidays]


def next_session(d: dt.date, holidays=()) -> dt.date:
    """First trading session strictly after `d`."""
    return np.busday_offset(d, 1, roll="forward", holidays=_hol(holidays)).astype(dt.date)


def last_session(d: dt.date, holidays=()) -> dt.date:
    """Latest trading session on or before `d`."""
    return np.busday_offset(d, 0, roll="backward", holidays=_hol(holidays)).astype(dt.date)


def previous_session(d: dt.date, holidays=()) -> dt.date:
    """Latest trading session strictly before `d`."""
    return np.busday_offset(d, -1, roll="backward", holidays=_hol(holidays)).astype(dt.date)


def sessions_between(a: dt.date, b: dt.date, holidays=()) -> int:
    """Trading sessions in [a, b) — how many sessions `a` is behind `b`."""
    return int(np.busday_count(a, b, holidays=_hol(holidays)))
