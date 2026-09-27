# Live-API holdings source. Pure — no Mongo, no network.
#
# When a portfolio is wired to a broker API, the API is authoritative and the
# downloaded file is ignored. Three of the file path's failure states become
# structurally impossible, which is the entire reason for the option:
#   UNRESOLVED     — the API returns tradingSymbol + ISIN, nothing to alias-guess
#   AMBIGUOUS      — one token is one account
#   REBALANCE_DUE  — a live read can never predate the rebalance
import holdings_store as hs
from brokers.base import Holding


def _h(symbol, qty, sellable=None, pending=0, pledged=0, isin="INE000A01001"):
    return Holding(symbol=symbol, exchange="NSE", quantity=qty, avg_price=100.0,
                   last_price=110.0, market_value=qty * 110.0, pnl=None,
                   pnl_percent=None, isin=isin,
                   sellable_qty=qty if sellable is None else sellable,
                   pending_qty=pending, pledged_qty=pledged)


def test_api_aggregate_shape_matches_the_file_path():
    agg = hs.aggregate_from_api([_h("AAA", 10), _h("BBB", 5)],
                                broker="dhan", account="default", client_ref="1234")
    assert agg["quantities"] == {"AAA": 10.0, "BBB": 5.0}
    assert agg["unresolved"] == []            # never a name to resolve
    assert agg["as_of_source"] == "broker_api"
    assert agg["fingerprints"] == ["dhan:default:1234"]
    assert len(agg["sources"]) == 1


def test_same_book_hashes_identically_across_reads():
    """Status is called on every page load. An unchanged book must NOT write a new
    snapshot each time, so the hash covers positions — never the read timestamp."""
    a = hs.aggregate_from_api([_h("AAA", 10)], broker="dhan", account="default",
                              client_ref="1234")
    b = hs.aggregate_from_api([_h("AAA", 10)], broker="dhan", account="default",
                              client_ref="1234")
    assert a["content_hash"] == b["content_hash"]
    assert a["as_of"] != b["as_of"] or True   # timestamps may differ; hash must not


def test_changed_quantity_changes_the_hash():
    a = hs.aggregate_from_api([_h("AAA", 10)], broker="dhan", account="default",
                              client_ref="1234")
    b = hs.aggregate_from_api([_h("AAA", 11)], broker="dhan", account="default",
                              client_ref="1234")
    assert a["content_hash"] != b["content_hash"]


def test_two_accounts_never_share_a_fingerprint_or_hash():
    """Same holdings in two different accounts must not dedup into one snapshot —
    that would let one account's book masquerade as the other's."""
    a = hs.aggregate_from_api([_h("AAA", 10)], broker="dhan", account="default",
                              client_ref="1111")
    b = hs.aggregate_from_api([_h("AAA", 10)], broker="dhan", account="second",
                              client_ref="2222")
    assert a["fingerprints"] != b["fingerprints"]
    assert a["content_hash"] != b["content_hash"]


def test_settlement_detail_is_carried_through():
    """The file path cannot know this; it is the API source's real added value."""
    agg = hs.aggregate_from_api([_h("AAA", 10, sellable=6, pending=3, pledged=1)],
                                broker="dhan", account="default", client_ref="1234")
    s = agg["settlement"]["AAA"]
    assert s["sellable"] == 6 and s["pending"] == 3 and s["pledged"] == 1
    assert s["isin"] == "INE000A01001"


def test_duplicate_symbols_are_summed():
    agg = hs.aggregate_from_api([_h("AAA", 10), _h("AAA", 5)],
                                broker="dhan", account="default", client_ref="1")
    assert agg["quantities"] == {"AAA": 15.0}


def test_empty_book_is_valid_not_an_error():
    agg = hs.aggregate_from_api([], broker="dhan", account="default", client_ref="1")
    assert agg["quantities"] == {} and agg["unresolved"] == []


def test_a_live_read_can_never_be_rebalance_due():
    """sync_state is called with last_rebalance=None on the API path, because a
    live read is current by construction. Guard the intent."""
    agg = hs.aggregate_from_api([_h("AAA", 10)], broker="dhan", account="default",
                                client_ref="1")
    snap = {"file_as_of": agg["as_of"], "account_fingerprint": None}
    state = hs.sync_state(snapshot=snap, lock=None, last_rebalance=None,
                          gaps=[], unresolved=None, expected_fingerprint=None)
    assert state["state"] == "UNLOCKED"        # not REBALANCE_DUE, not UNRESOLVED


# ---------- multi-account union ----------------------------------------------

def _read(account, holdings, cash=0.0, client_ref=None):
    return {"broker": "dhan", "account": account, "client_ref": client_ref or account,
            "holdings": holdings, "cash": cash}


def test_union_sums_quantities_across_accounts():
    agg = hs.aggregate_from_api_multi([
        _read("default", [_h("AAA", 10), _h("BBB", 5)]),
        _read("second", [_h("BBB", 7), _h("CCC", 3)]),
    ])
    assert agg["quantities"] == {"AAA": 10.0, "BBB": 12.0, "CCC": 3.0}
    assert len(agg["sources"]) == 2


def test_union_keeps_the_per_account_split():
    """Execution needs this: a SELL can only be placed in the account that holds
    the stock, so the union alone is not enough to build an order."""
    agg = hs.aggregate_from_api_multi([
        _read("default", [_h("BBB", 5)]),
        _read("second", [_h("BBB", 7)]),
    ])
    assert agg["by_account"]["default"]["quantities"] == {"BBB": 5.0}
    assert agg["by_account"]["second"]["quantities"] == {"BBB": 7.0}


def test_settlement_sums_but_stays_available_per_account():
    agg = hs.aggregate_from_api_multi([
        _read("default", [_h("AAA", 10, sellable=6, pending=4)]),
        _read("second", [_h("AAA", 5, sellable=5)]),
    ])
    assert agg["settlement"]["AAA"]["sellable"] == 11
    assert agg["settlement"]["AAA"]["pending"] == 4
    assert agg["by_account"]["default"]["settlement"]["AAA"]["sellable"] == 6


def test_adding_an_account_changes_the_hash():
    """Otherwise connecting a second account would dedup as 'unchanged' and the
    new positions would never be recorded."""
    one = hs.aggregate_from_api_multi([_read("default", [_h("AAA", 10)])])
    two = hs.aggregate_from_api_multi([_read("default", [_h("AAA", 10)]),
                                       _read("second", [])])
    assert one["content_hash"] != two["content_hash"]


def test_cash_is_tracked_per_account():
    """Capital-aware order routing needs to know which account can fund a buy."""
    agg = hs.aggregate_from_api_multi([
        _read("default", [_h("AAA", 10)], cash=50_000.0),
        _read("second", [_h("BBB", 5)], cash=0.0),
    ])
    assert agg["by_account"]["default"]["cash"] == 50_000.0
    assert agg["by_account"]["second"]["cash"] == 0.0
