# ISIN identity + settlement-state normalization. No network calls anywhere.
#
# Why this exists: both clients used to collapse every quantity into one number that
# INCLUDED stock which cannot be sold (Dhan read only totalQty; Kite added t1_quantity
# into the total). A SELL sized off that number is rejected by the broker, burns an
# execution slice and leaves the book unbalanced — most likely in the T+1 window right
# after a rebalance, which is exactly when we rebalance.
#
# Field names verified 2026-09-06 against the live docs:
#   Dhan (dhanhq.co/docs/v2/portfolio/): isin, totalQty, availableQty, t1Qty, collateralQty
#   Kite (kite.trade/docs/connect/v3/portfolio/): isin, quantity (realised T+2),
#        t1_quantity, collateral_quantity  <- pledged is "collateral_quantity",
#        there is NO "pledged_quantity" field.
from unittest import mock

from brokers.dhan import DhanClient
from brokers.kite import KiteClient


def _resp(payload, status=200):
    m = mock.Mock()
    m.status_code = status
    m.json.return_value = payload
    return m


def _kite(monkeypatch):
    c = KiteClient()
    c.api_key, c.api_secret = "k", "s"
    monkeypatch.setattr("brokers.config.read_cached_token", lambda name: "tok")
    return c


def _dhan():
    c = DhanClient()
    c.client_id, c.token = "cid", "tok"
    return c


# ---------- Dhan -------------------------------------------------------------

DHAN_FULL = [{"tradingSymbol": "INFY", "exchange": "NSE", "isin": "INE009A01021",
              "totalQty": 100, "availableQty": 60, "t1Qty": 30, "collateralQty": 10,
              "avgCostPrice": 1400.0, "lastTradedPrice": 1500.0}]


def test_dhan_maps_isin_and_settlement_state():
    with mock.patch("brokers.dhan.requests.get", return_value=_resp(DHAN_FULL)):
        [h] = _dhan().holdings()
    assert h.isin == "INE009A01021"
    assert h.quantity == 100          # total economic position — UNCHANGED behaviour
    assert h.sellable_qty == 60       # availableQty
    assert h.pending_qty == 30        # t1Qty — unsettled, cannot be sold
    assert h.pledged_qty == 10        # collateralQty


def test_dhan_missing_isin_is_empty_not_an_error():
    payload = [{**DHAN_FULL[0]}]
    payload[0].pop("isin")
    with mock.patch("brokers.dhan.requests.get", return_value=_resp(payload)):
        [h] = _dhan().holdings()
    assert h.isin == ""               # absent identity must not raise


def test_dhan_sellable_derived_when_available_qty_absent():
    """Older/partial payloads omit availableQty. Derive it from what we DO know
    (total - pending - pledged) rather than falling back to total, which would
    reintroduce the over-sell bug."""
    payload = [{**DHAN_FULL[0]}]
    payload[0].pop("availableQty")
    with mock.patch("brokers.dhan.requests.get", return_value=_resp(payload)):
        [h] = _dhan().holdings()
    assert h.sellable_qty == 60       # 100 total - 30 t1 - 10 collateral


def test_dhan_sellable_degrades_to_total_only_when_nothing_is_known():
    """No settlement fields at all -> today's behaviour, no regression."""
    payload = [{"tradingSymbol": "INFY", "exchange": "NSE", "totalQty": 100,
                "avgCostPrice": 1400.0, "lastTradedPrice": 1500.0}]
    with mock.patch("brokers.dhan.requests.get", return_value=_resp(payload)):
        [h] = _dhan().holdings()
    assert h.sellable_qty == 100 and h.pending_qty == 0 and h.pledged_qty == 0


# ---------- Kite -------------------------------------------------------------

KITE_FULL = {"status": "success", "data": [{
    "tradingsymbol": "TCS", "exchange": "NSE", "isin": "INE467B01029",
    "quantity": 70, "t1_quantity": 30, "collateral_quantity": 10,
    "average_price": 3500.0, "last_price": 3850.0, "pnl": 4200.0}]}


def test_kite_maps_isin_and_settlement_state(monkeypatch):
    with mock.patch("brokers.kite.requests.get", return_value=_resp(KITE_FULL)):
        [h] = _kite(monkeypatch).holdings()
    assert h.isin == "INE467B01029"
    assert h.quantity == 100          # quantity + t1_quantity — UNCHANGED behaviour
    assert h.pending_qty == 30        # t1_quantity
    assert h.pledged_qty == 10        # collateral_quantity (NOT "pledged_quantity")


def test_kite_sellable_excludes_pledged(monkeypatch):
    """Kite's `quantity` is realised T+2. Pledged stock cannot be sold, so subtract
    collateral_quantity. If collateral turns out NOT to be included in `quantity`,
    this under-reports sellable — which is the safe direction: it can only ever
    shrink a SELL, never oversize one. Confirm empirically against a live pledged
    holding (plan Task 10)."""
    with mock.patch("brokers.kite.requests.get", return_value=_resp(KITE_FULL)):
        [h] = _kite(monkeypatch).holdings()
    assert h.sellable_qty == 60       # 70 realised - 10 collateral


def test_kite_sellable_never_negative(monkeypatch):
    payload = {"status": "success", "data": [{
        "tradingsymbol": "TCS", "exchange": "NSE", "isin": "INE467B01029",
        "quantity": 5, "t1_quantity": 0, "collateral_quantity": 40,
        "average_price": 3500.0, "last_price": 3850.0}]}
    with mock.patch("brokers.kite.requests.get", return_value=_resp(payload)):
        [h] = _kite(monkeypatch).holdings()
    assert h.sellable_qty == 0        # clamped, never a negative sell size


def test_kite_missing_settlement_fields_degrade_safely(monkeypatch):
    payload = {"status": "success", "data": [{
        "tradingsymbol": "TCS", "exchange": "NSE",
        "quantity": 10, "t1_quantity": 2,
        "average_price": 3500.0, "last_price": 3850.0, "pnl": 4200.0}]}
    with mock.patch("brokers.kite.requests.get", return_value=_resp(payload)):
        [h] = _kite(monkeypatch).holdings()
    assert h.isin == ""
    assert h.quantity == 12           # matches the pre-existing normalization test
    assert h.sellable_qty == 10 and h.pending_qty == 2 and h.pledged_qty == 0


# ---------- serialization ----------------------------------------------------

def test_to_dict_carries_new_fields_for_the_plan_builder():
    """build_sync_plan consumes to_dict(); the sellable clamp in order_execution
    (Task 3) needs these keys to survive serialization."""
    with mock.patch("brokers.dhan.requests.get", return_value=_resp(DHAN_FULL)):
        [h] = _dhan().holdings()
    d = h.to_dict()
    assert d["isin"] == "INE009A01021"
    assert d["sellable_qty"] == 60 and d["pending_qty"] == 30 and d["pledged_qty"] == 10
