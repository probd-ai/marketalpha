"""Fresh LTP fetch via the existing TradingView client (tvdatafeed).

Used at sync-plan generation so LIMIT prices anchor to the LIVE price instead
of yesterday's close. Plan symbol counts are small (~40-60), one shared
websocket client, ~0.3-1s per symbol.

Fail-soft by design: any symbol that errors or exceeds the time budget is
simply absent from the result — callers fall back to holdings LTP / last close.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, float]] = {}  # symbol -> (fetched_at, ltp)
_LOCK = threading.Lock()
_client = None
CACHE_TTL_SEC = 90
TIME_BUDGET_SEC = 60


def _tv():
    global _client
    if _client is None:
        from tv_price_client import TvPriceClient
        _client = TvPriceClient()
    return _client


def _fetch_one(symbol: str) -> float | None:
    try:
        c = _tv()
        tv_symbol, exchange = c._resolve(symbol)
        df = c._client().get_hist(symbol=tv_symbol, exchange=exchange,
                                  interval=c._interval, n_bars=2)
        if df is None or df.empty:
            return None
        return float(df.iloc[-1]["close"])
    except Exception as e:
        logger.warning(f"fresh price fetch failed for {symbol}: {e}")
        return None


def get_ltp_map(symbols: list[str], max_age_sec: int = CACHE_TTL_SEC,
                time_budget_sec: int = TIME_BUDGET_SEC) -> dict[str, float]:
    """{symbol: fresh LTP} for whatever could be fetched within the budget.
    Serialized on one websocket client; results cached for max_age_sec."""
    now = time.time()
    out: dict[str, float] = {}
    missing: list[str] = []
    with _LOCK:
        for s in symbols:
            hit = _CACHE.get(s)
            if hit and now - hit[0] <= max_age_sec:
                out[s] = hit[1]
            else:
                missing.append(s)
    start = time.time()
    for s in missing:
        if time.time() - start > time_budget_sec:
            logger.warning(f"fresh price budget hit — {len(out)}/{len(symbols)} fetched")
            break
        px = _fetch_one(s)
        if px and px > 0:
            out[s] = px
            with _LOCK:
                _CACHE[s] = (time.time(), px)
    return out
