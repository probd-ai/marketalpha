"""end_date on price queries must include the whole calendar day.

Price bars are stored with intraday timestamps (e.g. 09:15), so a date-only
``end_date`` compared with ``$lte`` silently dropped the final session of every
requested window. These tests pin the inclusive-calendar-day contract.

Marked ``integration`` — requires a live MongoDB with market_hunt price data.
"""
import asyncio
from datetime import datetime

import pytest

pytestmark = pytest.mark.integration

SYMBOL = "RELIANCE"


def _run(coro):
    return asyncio.run(coro)


async def _fetch(symbol, start, end):
    from stock_data_manager import StockDataManager
    async with StockDataManager() as sm:
        return await sm.get_price_data(
            symbol=symbol, start_date=start, end_date=end, sort_order=1)


def test_end_date_includes_full_calendar_day():
    """A bar stamped 09:15 on the end_date must be returned for a midnight end_date."""
    async def scenario():
        # Find the latest available session first (no end filter).
        all_recs = await _fetch(SYMBOL, datetime(2026, 1, 1), None)
        assert all_recs, "no price data for test symbol"
        last_day = all_recs[-1].date.date()

        # Query with end_date == that calendar day at midnight.
        bounded = await _fetch(SYMBOL, datetime(2026, 1, 1),
                               datetime(last_day.year, last_day.month, last_day.day))
        return last_day, bounded[-1].date.date()

    last_day, bounded_last = _run(scenario())
    assert bounded_last == last_day, (
        f"end_date={last_day} dropped its own session: last returned bar {bounded_last}")


def test_end_date_does_not_leak_next_day():
    """Inclusive end_date must not pull bars from the following session."""
    async def scenario():
        all_recs = await _fetch(SYMBOL, datetime(2026, 1, 1), None)
        assert len(all_recs) >= 2, "need at least two sessions"
        second_last_day = all_recs[-2].date.date()
        bounded = await _fetch(SYMBOL, datetime(2026, 1, 1),
                               datetime(second_last_day.year, second_last_day.month,
                                        second_last_day.day))
        return second_last_day, bounded[-1].date.date()

    second_last_day, bounded_last = _run(scenario())
    assert bounded_last == second_last_day, (
        f"end_date={second_last_day} returned a later bar {bounded_last}")
