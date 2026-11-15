"""Streak watch (Grid-10 sleeves) and manual trades (overlay sells/buys recorded outside tickets)."""
import numpy as np
import pandas as pd
import pytest

from fund import service, watch


# ------------------------------------------------------------------------------------------- streak state (pure)
def _ml(values):
    return pd.Series(values, index=pd.bdate_range("2025-01-01", periods=len(values)), dtype=float)


def test_streak_state_counts_sessions_above_and_below():
    s = watch.streak_state(_ml([50.0] * 10 + [60.0] * 3), window=5)
    assert s["above"] is True and s["days"] == 3 and s["sma"] == pytest.approx(56.0)
    s = watch.streak_state(_ml([float(v) for v in range(50, 60)] + [40.0] * 4), window=5)   # rising, then a drop
    assert s["above"] is False and s["days"] == 4


def test_streak_state_without_enough_history_is_unknown():
    s = watch.streak_state(_ml([50.0] * 3), window=5)
    assert s["above"] is None and s["days"] == 0


def test_next_picks_follow_grid10_order_and_flag_below_sma():
    grid = {"A": 1, "B": 1, "C": -1, "D": 1, "H": 1}
    roc22 = {"A": -0.10, "B": -0.20, "C": -0.30, "D": 0.05, "H": -0.50}
    above = {"A": True, "B": False, "C": True, "D": True, "H": True}
    picks = watch.next_picks(["A", "B", "C", "D", "H"], held={"H"}, grid=grid, roc22=roc22, above=above)
    assert [(p["symbol"], p["eligible"]) for p in picks] == [("B", False), ("A", True), ("D", True)]


# ------------------------------------------------------------------------------------------- manual trades
class FakeStore:
    def __init__(self):
        self.manual, self.events = [], []
        self.cfg = {"version": 4, "sleeves": [{"id": "value", "label": "Value core", "kind": "grid10"}]}

    def get_config(self): return self.cfg
    def published_runs(self, version): return [{"_id": "R1"}]
    def executions(self, rid=None):
        return [{"run_id": "R1", "line_id": "L1", "sleeve": "Value core", "symbol": "MARUTI", "side": "BUY",
                 "status": "done", "qty": 10, "price": 12000.0}]
    def latest_run(self, version):
        return {"_id": "R1", "books": [{"label": "Value core", "holdings": [{"symbol": "MARUTI", "shares": 10, "price": 11386.0}]}]}
    def other_holdings(self): return []
    def journal(self, ev, d): self.events.append(ev)
    def add_manual_trade(self, doc): self.manual.append({"_id": f"M{len(self.manual)}", **doc}); return self.manual[-1]
    def manual_trades(self, version): return [m for m in self.manual if m["version"] == version]
    def delete_manual_trade(self, mid):
        before = len(self.manual)
        self.manual = [m for m in self.manual if m["_id"] != mid]
        return len(self.manual) < before


KNOWN = {"MARUTI", "BAJAJ-AUTO"}.__contains__


def test_manual_swap_is_counted_by_tracking():
    st = FakeStore()
    service.record_manual_trade(st, {"sleeve": "Value core", "symbol": "MARUTI", "side": "SELL", "qty": 10,
                                     "price": 11400.0, "note": "streak exit"}, KNOWN)
    service.record_manual_trade(st, {"sleeve": "Value core", "symbol": "BAJAJ-AUTO", "side": "BUY", "qty": 12,
                                     "price": 9500.0}, KNOWN)
    rows = {r["symbol"]: r for r in service.actual_book(st, price_of=lambda s: {"MARUTI": 11386.0, "BAJAJ-AUTO": 9500.0})["rows"]}
    assert rows["MARUTI"]["actual_qty"] == 0 and rows["MARUTI"]["gap_qty"] == -10
    assert rows["BAJAJ-AUTO"]["actual_qty"] == 12
    assert st.events.count("MANUAL_TRADE_RECORDED") == 2


@pytest.mark.parametrize("trade, msg", [
    ({"sleeve": "Growth", "symbol": "MARUTI", "side": "SELL", "qty": 1, "price": 1.0}, "sleeve"),
    ({"sleeve": "Value core", "symbol": "ZZZ", "side": "BUY", "qty": 1, "price": 1.0}, "unknown symbol"),
    ({"sleeve": "Value core", "symbol": "MARUTI", "side": "HOLD", "qty": 1, "price": 1.0}, "side"),
    ({"sleeve": "Value core", "symbol": "MARUTI", "side": "BUY", "qty": 0, "price": 1.0}, "qty"),
    ({"sleeve": "Value core", "symbol": "MARUTI", "side": "BUY", "qty": 1.5, "price": 1.0}, "qty"),
    ({"sleeve": "Value core", "symbol": "MARUTI", "side": "BUY", "qty": 1, "price": float("nan")}, "price"),
    ({"sleeve": "Value core", "symbol": "MARUTI", "side": "SELL", "qty": 11, "price": 1.0}, "holds"),   # oversell
])
def test_bad_manual_trades_refused(trade, msg):
    st = FakeStore()
    with pytest.raises(service.RunRefused, match=msg):
        service.record_manual_trade(st, trade, KNOWN)
    assert st.manual == []


def test_manual_trade_can_be_deleted():
    st = FakeStore()
    t = service.record_manual_trade(st, {"sleeve": "Value core", "symbol": "MARUTI", "side": "SELL", "qty": 10,
                                         "price": 11400.0}, KNOWN)
    service.delete_manual_trade(st, t["_id"])
    assert st.manual == [] and "MANUAL_TRADE_DELETED" in st.events
    with pytest.raises(service.RunRefused, match="not found"):
        service.delete_manual_trade(st, t["_id"])


# ------------------------------------------------------------------------------------------- real data (integration)
@pytest.mark.integration
def test_streak_watch_on_live_data():
    from pymongo import MongoClient

    from fund.store import FundStore
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    out = watch.streak_watch(db, FundStore(db))
    assert out["as_of"] and out["sleeves"]
    sl = out["sleeves"][0]
    assert {"symbol", "above", "days", "mean_long", "sma", "grid", "roc22", "exit"} <= set(sl["holdings"][0])
    assert all(np.isfinite(p["roc22"]) for p in sl["picks"])


def test_manual_trade_api_round_trip(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.routers.fund as fr
    st = FakeStore()

    class DB:
        class symbol_mappings:
            @staticmethod
            def find_one(q): return q if q.get("symbol") in {"MARUTI", "BAJAJ-AUTO"} else None

        class index_meta:
            @staticmethod
            def find_one(q): return None
    st.db = DB
    monkeypatch.setattr(fr, "_store", lambda: st)
    a = FastAPI()
    a.include_router(fr.router)
    c = TestClient(a, client=("127.0.0.1", 50000))
    body = {"sleeve": "Value core", "symbol": "MARUTI", "side": "SELL", "qty": 10, "price": 11400.0}
    assert c.post("/api/fund/manual-trades", json=body).status_code == 403          # desk header required
    h = {"X-Fund-Desk": "1"}
    assert c.post("/api/fund/manual-trades", json={**body, "qty": 99}, headers=h).status_code == 409
    tid = c.post("/api/fund/manual-trades", json=body, headers=h).json()["_id"]
    assert [t["_id"] for t in c.get("/api/fund/manual-trades").json()] == [tid]
    assert c.delete(f"/api/fund/manual-trades/{tid}", headers=h).json() == {"deleted": tid}
