import numpy as np
import pandas as pd
import pytest

from fund.engine.costs import Costs
from fund.engine.tranche import Tranche
from fund.engine.types import Lot, Reason, SleeveState

from helpers import make_market

ZERO = Costs(0.0, 0.0, 0.0)
SYMS = list("ABCDEFGH")


def _market(roc_vals, elig_vals=None, days=4):
    idx = pd.bdate_range("2021-01-04", periods=days)
    closes = pd.DataFrame(100.0, index=idx, columns=SYMS)
    roc = pd.DataFrame([roc_vals] * days, index=idx, columns=SYMS, dtype=float)
    elig = pd.DataFrame([elig_vals or [True] * len(SYMS)] * days, index=idx, columns=SYMS)
    return make_market(SYMS, days, closes=closes, roc=roc, elig=elig), idx


def _step(m, idx, st, book=None):
    return (book or Tranche(5, 8, 40, 0.10, False)).on_day(st, m, ZERO, idx, 1, idx[0], True)


def _lots(**kw):
    return {s: [Lot(s, q, pd.Timestamp("2021-01-01").date(), a) for q, a in v] for s, v in kw.items()}


def test_new_picks_get_one_tranche_each():
    m, idx = _market([0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01])
    st = SleeveState(cash=4000.0)
    orders = _step(m, idx, st)
    assert sorted(o.symbol for o in orders) == list("ABCDE")
    assert all(o.reason == Reason.TOP5_NEW_TRANCHE and o.value == pytest.approx(100.0) for o in orders)


def test_pick_already_at_cap_buys_nothing():
    m, idx = _market([0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01])
    st = SleeveState(cash=3600.0, lots=_lots(A=[(4.0, 1)]))   # A = 400 of 4000 = 10%
    orders = _step(m, idx, st)
    assert "A" not in {o.symbol for o in orders if o.side == "BUY"}


def test_expired_tranche_is_sold_and_a_repick_rolls_without_trading():
    m, idx = _market([0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01])
    st = SleeveState(cash=3800.0, lots=_lots(A=[(1.0, 8)], H=[(1.0, 8)]))  # both tranches expire; A re-picked
    orders = {(o.symbol, o.side): o for o in _step(m, idx, st)}
    assert orders[("H", "SELL")].reason == Reason.TRANCHE_EXPIRED
    assert ("A", "SELL") not in orders and ("A", "BUY") not in orders       # 1 share expiring == new 1-share tranche
    assert st.lots["A"][0].age == 1 and st.lots["A"][0].qty == pytest.approx(1.0)


def test_trim_shrinks_every_lot_pro_rata():
    m, idx = _market([0.0] * 8, elig_vals=[False] * 8)
    st = SleeveState(cash=800.0, lots=_lots(A=[(1.0, 1), (1.0, 2)]))      # A = 200 of 1000 = 20%
    orders = _step(m, idx, st)
    assert orders[0].reason == Reason.TRIM_ABOVE_CAP and orders[0].qty == pytest.approx(1.0)
    assert [l.qty for l in st.lots["A"]] == pytest.approx([0.5, 0.5])


def test_cash_deploy_respects_cap_and_rank():
    m, idx = _market([0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01])
    st = SleeveState(cash=1000.0)
    orders = _step(m, idx, st, Tranche(5, 8, 40, 0.10, True))
    dep = [o for o in orders if o.reason == Reason.CASH_DEPLOY]
    assert [o.symbol for o in dep] == list("ABCDE")                           # rank order
    assert all(st.qty(s) * 100 == pytest.approx(100.0) for s in "ABCDE")      # 10% cap of 1000


def test_halted_expiring_tranche_waits():
    m, idx = _market([0.0] * 8, elig_vals=[False] * 8)
    m.opens.loc[idx[1], "A"] = np.nan
    st = SleeveState(cash=900.0, lots=_lots(A=[(1.0, 8)]))
    assert _step(m, idx, st) == []
    assert st.lots["A"][0].age == 9                                          # re-queued at `weeks`, then aged


def test_buys_are_scaled_when_cash_is_short():
    m, idx = _market([0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01])
    st = SleeveState(cash=100.0, lots=_lots(H=[(39.0, 1)]))                 # equity 4000, slot 100, cash 100
    orders = [o for o in _step(m, idx, st, Tranche(5, 8, 40, None, False)) if o.side == "BUY"]
    assert sum(o.value for o in orders) == pytest.approx(100.0)
    assert all(o.value == pytest.approx(20.0) for o in orders)


def test_index_exit_sold_same_day():
    idx = pd.bdate_range("2021-01-04", periods=4)
    m, _ = _market([0.0] * 8, elig_vals=[False] * 8)
    m.members = lambda d: set(SYMS) - {"A"}
    st = SleeveState(cash=0.0, lots=_lots(A=[(1.0, 1)]))
    orders = Tranche().on_day(st, m, ZERO, idx, 1, idx[0], False)
    assert orders[0].reason == Reason.INDEX_EXIT and "A" not in st.lots


# ---------------------------------------------------------------- trim band: trim only above trim_at, back to max_w
def _held(weights_pct):
    """A sleeve of 1000 holding A..: each at the given % of equity (price 100), rest in cash."""
    lots = {s: [(w * 10 / 100.0, 1)] for s, w in zip("ABCDE", weights_pct)}
    st = SleeveState(cash=1000.0 - sum(w * 10 for w in weights_pct), params={})
    st.lots = _lots(**lots)
    return st


def test_default_trims_anything_above_the_cap():
    m, idx = _market([0.0] * 8)
    st = _held([12])
    orders = _step(m, idx, st, Tranche(5, 8, 40, 0.10, False))
    assert any(o.symbol == "A" and o.reason == Reason.TRIM_ABOVE_CAP for o in orders)


def test_band_leaves_a_holding_between_cap_and_trim_at_alone():
    m, idx = _market([0.0] * 8)
    st = _held([12])
    orders = _step(m, idx, st, Tranche(5, 8, 40, 0.10, False, trim_at=0.15))
    assert not [o for o in orders if o.reason == Reason.TRIM_ABOVE_CAP]
    assert st.qty("A") == pytest.approx(1.2)


def test_band_trims_back_to_the_cap_once_above_trim_at():
    m, idx = _market([0.0] * 8)
    st = _held([16])
    orders = _step(m, idx, st, Tranche(5, 8, 40, 0.10, False, trim_at=0.15))
    trims = [o for o in orders if o.reason == Reason.TRIM_ABOVE_CAP]
    assert [o.symbol for o in trims] == ["A"]
    assert st.qty("A") * 100 == pytest.approx(0.10 * 1000.0)        # back to 10%, not 15%


# ---------------------------------------------------------------- full_slot: a new tranche is always a full slot
def _full_book(**kw):
    return Tranche(5, 8, 40, 0.5, False, **kw)          # cap 50% so the cap trim stays out of the way


def _invested():
    st = SleeveState(cash=0.0)                           # fully invested: F 40%, G 30%, H 30%, no cash
    st.lots = _lots(F=[(40.0, 1)], G=[(30.0, 1)], H=[(30.0, 1)])
    return st


def test_default_scales_new_tranches_to_the_cash_available():
    m, idx = _market([0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01])
    st = _invested()
    _step(m, idx, st, _full_book())
    assert sum(st.qty(s) for s in "ABCDE") == pytest.approx(0.0)          # no cash -> no new money


def test_full_slot_trims_the_largest_holdings_to_fund_full_tranches():
    m, idx = _market([0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01])
    st = _invested()
    orders = _step(m, idx, st, _full_book(full_slot=True))
    slot = 10_000.0 / 40                                                  # 2.5% of the 10,000 sleeve
    assert all(st.qty(s) * 100 == pytest.approx(slot) for s in "ABCDE")
    # 1,250 raised by water-filling from the top: F, G, H all cut to the same level
    assert [round(st.qty(s) * 100, 2) for s in "FGH"] == [pytest.approx(2916.67, abs=0.01)] * 3
    assert {o.symbol for o in orders if o.reason == Reason.TRIM_TO_FUND} == set("FGH")
    assert st.cash == pytest.approx(0.0, abs=1e-6)


def test_full_slot_takes_from_the_single_largest_first():
    m, idx = _market([0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01])
    st = SleeveState(cash=0.0)
    st.lots = _lots(F=[(60.0, 1)], G=[(20.0, 1)], H=[(20.0, 1)])
    _step(m, idx, st, _full_book(full_slot=True))
    assert st.qty("F") * 100 == pytest.approx(6000.0 - 1250.0)            # only the largest is cut
    assert st.qty("G") == pytest.approx(20.0) and st.qty("H") == pytest.approx(20.0)


def test_full_slot_with_enough_cash_trims_nothing():
    m, idx = _market([0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01])
    st = SleeveState(cash=4000.0)
    orders = _step(m, idx, st, _full_book(full_slot=True))
    assert not [o for o in orders if o.reason == Reason.TRIM_TO_FUND]
