"""NSE trading-holiday calendar: parsing the holiday-master payload, storing it, and session arithmetic."""
import datetime as dt

import pytest

import market_calendar as mc

# shape of https://www.nseindia.com/api/holiday-master?type=trading (fields as served 2026-10-04)
PAYLOAD = {
    "CM": [
        {"tradingDate": "02-Oct-2026", "weekDay": "Friday", "description": "Mahatma Gandhi Jayanti", "Sr_no": 15},
        {"tradingDate": "20-Oct-2026", "weekDay": "Tuesday", "description": "Dussehra", "Sr_no": 16},
        {"tradingDate": "08-Nov-2026", "weekDay": "Sunday", "description": "Diwali Laxmi Pujan*", "Sr_no": 17},
    ],
    "FO": [{"tradingDate": "01-Jan-2026", "weekDay": "Thursday", "description": "not equity", "Sr_no": 1}],
}
HOL = {dt.date(2026, 10, 2), dt.date(2026, 10, 20), dt.date(2026, 11, 8)}


def test_parse_takes_the_equity_segment_only():
    rows = mc.parse_holidays(PAYLOAD)
    assert [(r["date"], r["description"]) for r in rows] == [
        (dt.date(2026, 10, 2), "Mahatma Gandhi Jayanti"), (dt.date(2026, 10, 20), "Dussehra"),
        (dt.date(2026, 11, 8), "Diwali Laxmi Pujan*")]


@pytest.mark.parametrize("bad", [{}, {"CM": []}, {"CM": [{"tradingDate": "garbage"}]}, None])
def test_parse_refuses_an_unusable_payload(bad):
    with pytest.raises(ValueError):
        mc.parse_holidays(bad)


def test_next_session_skips_weekends_and_holidays():
    assert mc.next_session(dt.date(2026, 10, 1), HOL) == dt.date(2026, 10, 5)     # Thu -> Fri holiday -> Mon
    assert mc.next_session(dt.date(2026, 10, 19), HOL) == dt.date(2026, 10, 21)   # Mon -> Tue Dussehra -> Wed
    assert mc.next_session(dt.date(2026, 9, 24), HOL) == dt.date(2026, 9, 25)
    assert mc.next_session(dt.date(2026, 10, 1), set()) == dt.date(2026, 10, 2)   # no calendar: weekdays only


def test_last_session_on_or_before():
    assert mc.last_session(dt.date(2026, 10, 4), HOL) == dt.date(2026, 10, 1)     # Sun -> Sat -> Fri holiday -> Thu
    assert mc.last_session(dt.date(2026, 10, 2), HOL) == dt.date(2026, 10, 1)
    assert mc.last_session(dt.date(2026, 10, 5), HOL) == dt.date(2026, 10, 5)


def test_previous_session_is_strictly_before():
    assert mc.previous_session(dt.date(2026, 10, 5), HOL) == dt.date(2026, 10, 1)
    assert mc.previous_session(dt.date(2026, 9, 28), HOL) == dt.date(2026, 9, 25)


def test_sessions_between_counts_trading_days():
    assert mc.sessions_between(dt.date(2026, 10, 1), dt.date(2026, 10, 5), HOL) == 1
    assert mc.sessions_between(dt.date(2026, 9, 25), dt.date(2026, 10, 5), HOL) == 5   # 25, 28, 29, 30, 1


class FakeColl:
    def __init__(self): self.docs = {}
    def replace_one(self, key, doc, upsert=False): self.docs[key["_id"]] = doc
    def find(self, q=None, proj=None): return list(self.docs.values())


def test_store_and_load_round_trip():
    db = {"market_holidays": FakeColl()}
    n = mc.store_holidays(db, mc.parse_holidays(PAYLOAD))
    assert n == 3
    assert mc.load_holidays(db) == HOL
    assert mc.years_covered(db) == {2026}


@pytest.mark.integration
def test_live_nse_calendar_has_gandhi_jayanti():
    import asyncio
    rows = mc.parse_holidays(asyncio.run(mc.fetch_holiday_master()))
    assert any(r["date"] == dt.date(2026, 10, 2) for r in rows)
