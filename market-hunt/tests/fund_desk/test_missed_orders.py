"""Model trades between run days (index exits on any session) must reach the next ticket.

ASKAUTOLTD left the Total Market index effective Wed 2026-09-30; the engine sold it that day, but the ticket only
carried the run day's orders, so the manager was never told to sell."""
import pandas as pd
import pytest

from fund.engine.costs import Costs
from fund.engine.replay import replay_sleeve
from fund.engine.tranche import Tranche
from fund.engine.types import Order, Reason
from fund.ticket import build_ticket, held_before_ticket, orders_since

from helpers import make_market

ZERO = Costs(0.0, 0.0, 0.0)


def test_orders_since_keeps_only_sessions_after_the_last_fill():
    d = pd.bdate_range("2026-09-28", periods=4)                     # Mon..Thu
    a, b, c = (Order("A", "SELL", 1, 10.0, 10.0, Reason.INDEX_EXIT), Order("B", "BUY", 2, 5.0, 10.0,
               Reason.TOP5_NEW_TRANCHE), Order("C", "SELL", 3, 1.0, 3.0, Reason.INDEX_EXIT))
    got = orders_since({d[0]: [b], d[2]: [a], d[3]: [c]}, after=d[0])
    assert [(day, o.symbol) for day, o in got] == [(d[2], "A"), (d[3], "C")]
    assert orders_since({d[2]: [a]}, after=None) == []


def test_held_before_adds_back_what_the_model_already_traded():
    got = held_before_ticket({"B": 4.0}, [(None, Order("A", "SELL", 7, 1.0, 7.0, Reason.INDEX_EXIT)),
                                          (None, Order("B", "BUY", 1, 1.0, 1.0, Reason.CASH_DEPLOY))])
    assert got == {"A": 7.0, "B": 3.0}


def test_missed_exit_becomes_a_full_sell_line_with_its_date():
    exit_order = Order("ASK", "SELL", 8.6, 600.0, 5160.0, Reason.INDEX_EXIT)
    lines, _ = build_ticket({"Satellite": {
        "orders": [exit_order], "scale": 10.0, "held_before": {"ASK": 8.6}, "held_after": {},
        "reset_factor": None, "ctx": {"ASK": {"exit_date": "30 Sep"}}}}, {"ASK": 618.15})
    (l,) = lines
    assert (l.side, l.symbol, l.qty, l.full_exit) == ("SELL", "ASK", 86, True)
    assert "30 Sep" in l.reason_text and "index" in l.reason_text.lower()


def test_replay_records_a_midweek_index_exit_that_orders_since_recovers():
    syms = list("ABCDEF")
    idx = pd.bdate_range("2026-09-21", periods=10)                  # Mon 09-21 .. Fri 10-02
    closes = pd.DataFrame(100.0, index=idx, columns=syms)
    roc = pd.DataFrame([[0.06, 0.05, 0.04, 0.03, 0.02, 0.01]] * len(idx), index=idx, columns=syms)
    elig = pd.DataFrame(True, index=idx, columns=syms)
    gone = pd.Timestamp("2026-09-30").date()                          # Wednesday: A leaves the index
    members = lambda d: set(syms) - ({"A"} if d >= gone else set())
    m = make_market(syms, len(idx), closes=closes, roc=roc, elig=elig, members=members)
    mondays = {d for d in idx if d.weekday() == 0}
    res = replay_sleeve(Tranche(5, 8, 40, 0.10, False), m, ZERO, idx[1], idx[-1], mondays, 1000.0)  # idx[0] = history
    last_fill = pd.Timestamp("2026-09-28")
    missed = orders_since(res.orders, after=last_fill)
    assert [(d.date(), o.symbol, o.reason) for d, o in missed] == [(gone, "A", Reason.INDEX_EXIT)]


@pytest.mark.integration
def test_live_v3_scenario_tickets_the_askautoltd_exit():
    """Real data: the published 2026-09-25 build (version 3) replayed to 2026-10-01 must ticket the 09-30 index
    exit of ASKAUTOLTD. Skips if that published run is not in this database."""
    import copy

    from pymongo import MongoClient

    from fund import service
    from fund.store import FundStore
    cli = MongoClient("mongodb://localhost:27017/")
    mh = cli["market_hunt"]
    pub = mh.fund_runs.find_one({"_id": "R20260925-1a1594"})
    if pub is None:
        pytest.skip("published run R20260925-1a1594 not present")
    cli.drop_database("fund_missed_test")
    try:
        st = FundStore(cli["fund_missed_test"])
        v3 = {k: v for k, v in st.get_config().items()}
        v3.update({"version": 3, "capital": 4_500_000.0, "model_start": "2026-01-01", "rebalance_months": [1, 7],
                   "rebalance_band": 0.05, "sleeves": [
                       {"id": "value", "label": "Value core", "kind": "grid10", "universe": "NIFTY50", "weight": 0.28,
                        "params": {"n": 10}},
                       {"id": "gold", "label": "Gold", "kind": "asset", "symbol": "GOLDBEES", "weight": 0.08},
                       {"id": "ndx", "label": "Nasdaq-100", "kind": "asset", "symbol": "MON100", "weight": 0.04},
                       {"id": "growth", "label": "Growth", "kind": "tranche", "universe": "NIFTY200", "weight": 0.35,
                        "params": {"top": 5, "weeks": 8, "slot_div": 40, "max_w": 0.1}},
                       {"id": "satellite", "label": "Satellite", "kind": "tranche", "universe": "NIFTY500MICRO750",
                        "weight": 0.25, "params": {"top": 5, "weeks": 8, "slot_div": 40, "max_w": 0.1}}]})
        st.db.fund_config.replace_one({"_id": "main"}, v3, upsert=True)
        st.db.fund_runs.insert_one(copy.deepcopy(pub))
        snap = service.compute_run(mh, st)
        ask = [l for l in snap["ticket"] if l["symbol"] == "ASKAUTOLTD"]
        assert len(ask) == 1, [l["symbol"] for l in snap["ticket"]]
        l = ask[0]
        bought = next(x["qty"] for x in pub["ticket"] if x["symbol"] == "ASKAUTOLTD")
        # full exit = "(exit all)" on the ticket; qty is the model's count, as for every full exit (82 bought vs 86)
        assert l["side"] == "SELL" and l["full_exit"] and abs(l["qty"] - bought) <= 0.1 * bought
        assert "30 Sep" in l["reason_text"] and "INDEX_EXIT" in l["reasons"]
        assert not [c for c in snap["checks"] if c["name"] == "Drift" and c["status"] != "PASS"]
    finally:
        cli.drop_database("fund_missed_test")
