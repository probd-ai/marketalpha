"""Extra fills, price warnings, other holdings and undeployed capital
(spec docs/specs/2026-10-04-fund-holdings-transfer-in.md, v2)."""
import math

import pytest

from fund import service


class FakeStore:
    """In-memory stand-in for FundStore: one published run with a BUY and a SELL line."""

    def __init__(self, status="PUBLISHED"):
        self.run = {"_id": "R1", "status": status, "fill_date": "2026-09-28", "ticket": [
            {"line_id": "L001", "sleeve": "Growth", "symbol": "AAA", "side": "BUY", "qty": 10, "ref_px": 100.0},
            {"line_id": "L002", "sleeve": "Growth", "symbol": "BBB", "side": "SELL", "qty": 5, "ref_px": 50.0}]}
        self.recs, self.events, self.other = {}, [], {}

    def get_run(self, rid): return self.run if rid == "R1" else None
    def record_execution(self, rid, rec): self.recs[rec["line_id"]] = {"run_id": rid, **rec}
    def executions(self, rid=None): return list(self.recs.values())
    def set_status(self, rid, st, **kw): self.run["status"] = st
    def journal(self, ev, d): self.events.append((ev, d))

    def set_other_holding(self, symbol, doc):
        if doc is None:
            self.other.pop(symbol, None)
        else:
            self.other[symbol] = doc

    def other_holdings(self): return [{"symbol": s, **d} for s, d in sorted(self.other.items())]


RANGE = {"AAA": (95.0, 108.0), "BBB": (48.0, 52.0)}            # traded low-high since the fill date


def rec(items, st=None, confirm=False, ranges=RANGE):
    st = st or FakeStore()
    service.record_executions(st, "R1", items, price_range=lambda syms, since: {s: ranges[s] for s in syms if s in ranges},
                              confirm=confirm)
    return st


# ------------------------------------------------------------------------------------------- statuses
def test_extra_records_full_quantity_and_the_surplus():
    st = rec([{"line_id": "L001", "status": "extra", "qty": 13, "price": 101.0}])
    r = st.recs["L001"]
    assert (r["status"], r["qty"], r["extra_qty"], r["price"]) == ("extra", 13, 3, 101.0)


def test_extra_counts_as_recorded_and_closes_the_run():
    st = rec([{"line_id": "L001", "status": "extra", "qty": 11, "price": 100.0},
              {"line_id": "L002", "status": "skipped"}])
    assert st.run["status"] == "CLOSED"


@pytest.mark.parametrize("item, msg", [
    ({"line_id": "L001", "status": "extra", "qty": 10, "price": 100.0}, "more than"),     # not more than the ticket
    ({"line_id": "L001", "status": "extra", "price": 100.0}, "more than"),
    ({"line_id": "L002", "status": "extra", "qty": 9, "price": 50.0}, "BUY"),             # sells can't be extra
    ({"line_id": "L001", "status": "done", "price": 0}, "fill price"),
    ({"line_id": "L001", "status": "done", "price": math.nan}, "fill price"),
    ({"line_id": "L001", "status": "partial", "qty": 10, "price": 100.0}, "partial"),
    ({"line_id": "L001", "status": "filled", "price": 100.0}, "status"),
])
def test_bad_records_refused(item, msg):
    with pytest.raises(service.RunRefused, match=msg):
        rec([item])


def test_existing_statuses_unchanged():
    st = rec([{"line_id": "L001", "status": "partial", "qty": 3, "price": 99.0},
              {"line_id": "L002", "status": "done", "price": 50.5}])
    assert (st.recs["L001"]["qty"], st.recs["L002"]["qty"]) == (3, 5)
    assert "extra_qty" not in st.recs["L001"] and "price_warning" not in st.recs["L001"]


# ------------------------------------------------------------------------------------------- price warnings
def test_price_outside_traded_range_warns_and_writes_nothing():
    st = FakeStore()
    with pytest.raises(service.PriceWarnings) as e:
        rec([{"line_id": "L001", "status": "done", "price": 110.0},          # above the 108 high
             {"line_id": "L002", "status": "done", "price": 50.0}], st)
    w = e.value.warnings
    assert [x["line_id"] for x in w] == ["L001"]
    assert w[0]["low"] == 95.0 and w[0]["high"] == 108.0 and "traded" in w[0]["reason"]
    assert st.recs == {} and st.events == []


def test_more_than_20pct_from_reference_warns_instead_of_refusing():
    with pytest.raises(service.PriceWarnings) as e:
        rec([{"line_id": "L001", "status": "done", "price": 79.0}], ranges={})       # no range data: 20% rule only
    assert "20%" in e.value.warnings[0]["reason"]


def test_confirm_saves_and_marks_the_warning():
    st = rec([{"line_id": "L001", "status": "done", "price": 79.0}], confirm=True)
    r = st.recs["L001"]
    assert r["price"] == 79.0 and r["price_warning"]


def test_in_range_price_saves_without_confirm():
    st = rec([{"line_id": "L001", "status": "done", "price": 107.9}])
    assert "price_warning" not in st.recs["L001"]


# ------------------------------------------------------------------------------------------- other holdings
def test_set_other_holdings_validates_and_removes():
    st = FakeStore()
    known = {"AAA", "GOLD"}.__contains__
    service.set_other_holdings(st, {"aaa": {"qty": 5, "cost_basis": 90.0, "note": "old demat"}}, known)
    assert st.other["AAA"] == {"qty": 5, "cost_basis": 90.0, "note": "old demat"}
    assert any(ev == "OTHER_HOLDING_UPDATED" for ev, _ in st.events)
    for bad, msg in [({"ZZZ": {"qty": 1}}, "unknown symbol"), ({"AAA": {"qty": -1}}, "qty"),
                     ({"AAA": {"qty": 1.5}}, "qty"), ({"AAA": {"qty": 1, "cost_basis": -2}}, "cost_basis")]:
        with pytest.raises(service.RunRefused, match=msg):
            service.set_other_holdings(st, bad, known)
    service.set_other_holdings(st, {"AAA": {"qty": 0}}, known)
    assert st.other == {}


# ------------------------------------------------------------------------------------------- tracking
class TrackStore(FakeStore):
    def __init__(self, execs, books, other=None):
        super().__init__()
        self._execs = execs
        self._latest = {"_id": "R9", "status": "PUBLISHED", "books": books}
        self.other = other or {}

    def get_config(self): return {"version": 3}
    def published_runs(self, version): return [{"_id": "R1"}]
    def executions(self, rid=None): return self._execs
    def latest_run(self, version): return self._latest
    def manual_trades(self, version): return []


def _ex(sym, qty, price, sleeve="Growth", side="BUY", status="done", **kw):
    return {"run_id": "R1", "line_id": "L001", "sleeve": sleeve, "symbol": sym, "side": side, "status": status,
            "qty": qty, "price": price, **kw}


def test_tracking_summarises_under_and_over_invested_per_sleeve():
    books = [{"label": "Growth", "holdings": [{"symbol": "AAA", "shares": 10, "price": 100.0},
                                              {"symbol": "CCC", "shares": 4, "price": 50.0}]},
             {"label": "Value core", "holdings": [{"symbol": "DDD", "shares": 2, "price": 10.0}]}]
    execs = [_ex("AAA", 12, 100.0), _ex("DDD", 2, 10.0, sleeve="Value core")]       # AAA +2 over; CCC skipped
    closes = {"AAA": 110.0, "CCC": 55.0, "DDD": 10.0}
    out = service.actual_book(TrackStore(execs, books), price_of=lambda syms: {s: closes[s] for s in syms if s in closes})
    g = next(s for s in out["sleeves"] if s["sleeve"] == "Growth")
    assert g["model_value"] == pytest.approx(10 * 110 + 4 * 55)
    assert g["actual_value"] == pytest.approx(12 * 110)
    assert g["under_value"] == pytest.approx(4 * 55)
    assert g["over_value"] == pytest.approx(2 * 110)
    assert out["undeployed"] == pytest.approx(4 * 55)


def test_extra_surplus_is_not_a_fund_position_but_an_other_holding():
    books = [{"label": "Growth", "holdings": [{"symbol": "AAA", "shares": 10, "price": 100.0}]}]
    st = TrackStore([_ex("AAA", 13, 101.0, status="extra", extra_qty=3)], books)
    out = service.actual_book(st, price_of=lambda syms: {"AAA": 110.0})
    assert out["rows"][0]["actual_qty"] == 10 and out["rows"][0]["gap_qty"] == 0
    other = service.other_holdings_view(st, price_of=lambda syms: {"AAA": 110.0})
    assert other["from_tickets"] == [{"symbol": "AAA", "qty": 3, "price": 110.0, "value": pytest.approx(330.0),
                                      "cost_basis": 101.0, "source": "R1 L001"}]
    assert out["other_value"] == pytest.approx(330.0)


def test_other_holdings_view_values_manual_entries():
    st = TrackStore([], [], other={"GOLD": {"qty": 7, "cost_basis": 80.0, "note": "SGB"}})
    v = service.other_holdings_view(st, price_of=lambda syms: {"GOLD": 110.0})
    assert v["manual"] == [{"symbol": "GOLD", "qty": 7, "cost_basis": 80.0, "note": "SGB", "price": 110.0,
                            "value": pytest.approx(770.0)}]
    assert v["value"] == pytest.approx(770.0)


def test_tracking_values_a_position_the_model_no_longer_holds():
    out = service.actual_book(TrackStore([_ex("EEE", 3, 20.0)], [{"label": "Growth", "holdings": []}]),
                              price_of=lambda syms: {"EEE": 25.0})
    assert (out["rows"][0]["gap_qty"], out["rows"][0]["gap_value"]) == (3, pytest.approx(75.0))


def test_tracking_falls_back_to_model_price_without_a_close():
    books = [{"label": "Growth", "holdings": [{"symbol": "AAA", "shares": 10, "price": 100.0}]}]
    out = service.actual_book(TrackStore([], books))
    assert out["undeployed"] == pytest.approx(1000.0)


# ------------------------------------------------------------------------------------------- real Mongo (integration)
@pytest.mark.integration
def test_store_other_holdings_and_latest_run_skip_discarded():
    from pymongo import MongoClient

    from fund.store import FundStore
    cli = MongoClient("mongodb://localhost:27017/")
    cli.drop_database("fund_transfer_test")
    try:
        st = FundStore(cli["fund_transfer_test"])
        st.set_other_holding("AAA", {"qty": 7, "cost_basis": 80.0})
        st.set_other_holding("BBB", {"qty": 1})
        assert [(o["symbol"], o["qty"]) for o in st.other_holdings()] == [("AAA", 7), ("BBB", 1)]
        st.set_other_holding("AAA", None)
        assert [o["symbol"] for o in st.other_holdings()] == ["BBB"]
        st.db.fund_runs.insert_many([
            {"_id": "R1", "version": 1, "status": "PUBLISHED", "created_at": "2026-10-01T00:00:00+00:00"},
            {"_id": "R2", "version": 1, "status": "DISCARDED", "created_at": "2026-10-02T00:00:00+00:00"}])
        assert st.latest_run(1)["_id"] == "R1"
    finally:
        cli.drop_database("fund_transfer_test")


@pytest.mark.integration
def test_latest_closes_and_traded_range_read_real_bars():
    from datetime import datetime

    from pymongo import MongoClient

    from fund.data import latest_closes, traded_range
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    out = latest_closes(db, ["ETERNAL", "NO_SUCH_SYMBOL"])
    bar = db["prices_2025_2029"].find_one({"symbol": "ETERNAL"}, sort=[("date", -1)])
    assert set(out) == {"ETERNAL"} and out["ETERNAL"] == pytest.approx(bar["close_price"])
    rng = traded_range(db, ["PAYTM"], "2026-09-28")
    bars = list(db["prices_2025_2029"].find({"symbol": "PAYTM", "date": {"$gte": datetime(2026, 9, 28)}}))
    assert rng["PAYTM"] == (pytest.approx(min(b["low_price"] for b in bars)),
                            pytest.approx(max(b["high_price"] for b in bars)))


# ------------------------------------------------------------------------------------------- API: warning round trip
def test_api_returns_warnings_then_saves_on_confirm(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.routers.fund as fr
    st = FakeStore()
    st.db = None
    monkeypatch.setattr(fr, "_store", lambda: st)
    monkeypatch.setattr(fr, "traded_range", lambda db, syms, since: {s: RANGE[s] for s in syms})
    a = FastAPI()
    a.include_router(fr.router)
    c = TestClient(a, client=("127.0.0.1", 50000))
    body = [{"line_id": "L001", "status": "extra", "qty": 12, "price": 120.0}]
    h = {"X-Fund-Desk": "1"}
    r = c.post("/api/fund/runs/R1/executions", json=body, headers=h)
    assert r.status_code == 409
    w = r.json()["detail"]["warnings"]
    assert w[0]["line_id"] == "L001" and w[0]["high"] == 108.0
    assert st.recs == {}
    r = c.post("/api/fund/runs/R1/executions?confirm=true", json=body, headers=h)
    assert r.status_code == 200 and st.recs["L001"]["extra_qty"] == 2 and st.recs["L001"]["price_warning"]
