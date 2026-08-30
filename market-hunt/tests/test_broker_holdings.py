"""Parsing and reconciling broker holdings exports (Dhan / Kite).

Two hazards drive most of these tests:

* Dhan exports identify stocks by DISPLAY NAME, not ticker ("Great Eastern Shipping
  Company", whose nse_name is "THE GE SHPG.LTD"). A wrong guess reconciles the wrong
  stock and would become a wrong trade, so resolution must be exact-or-block. Never
  fuzzy.
* Dhan exports carry PII in a header block (holder name, UCC, mobile, email). None of
  it may be persisted; the account is tracked by a one-way fingerprint instead.

Quantities are the only thing reconciled. The broker's LTP is from whenever the user
downloaded the file and our prices are from the last data refresh, so every value
comparison would differ for reasons that mean nothing.
"""
import datetime as dt

import pytest

from broker_holdings import (
    Holding,
    detect_format,
    normalize_name,
    parse_dhan,
    parse_kite,
    reconcile,
)

DHAN = """EQ,For 30-08-2026
Name,SOMEONE
UCC,ABCD12345X
Mobile,9999999999
Email ID,someone@example.com

Scrip Name,Quantity,Avg. Buy Rate,Buy Value,LTP,Current Value,P&L,P&L%
"Data Patterns","13","3351.58","43570.60","4624.80","60122.40","16551.80","37.99"
"Ather Energy","40","779.65","31186.00","1616.30","64652.00","33466.00","107.31"

Investment,74756.6,Current Value,124774.4,Overall P&L,50017.8,Overall P&L%,66.9,Today's P&L,10.0,Today's P&L%,0.1

NOTE : This sheet was downloaded at 30/08/2026 19:18
"""

KITE = '''"Instrument","Qty.","Avg. cost","LTP","Invested","Cur. val","P&L","Net chg.","Day chg.",""
"BAJAJ-AUTO",12,11510.58,11920,138127,143040,4913,3.56,2.1,""
"DIVISLAB",30,8537.44,9269,256123.1,278070,21946.9,8.57,2.42,""
'''


class TestDetectFormat:
    def test_dhan(self):
        assert detect_format(DHAN) == "dhan"

    def test_kite(self):
        assert detect_format(KITE) == "kite"

    def test_unknown_is_none_not_a_guess(self):
        assert detect_format("a,b,c\n1,2,3\n") is None
        assert detect_format("") is None


class TestParseDhan:
    def test_extracts_holdings_only(self):
        snap = parse_dhan(DHAN)
        assert [h.raw_name for h in snap.holdings] == ["Data Patterns", "Ather Energy"]
        assert [h.quantity for h in snap.holdings] == [13.0, 40.0]
        assert snap.holdings[0].avg_cost == 3351.58

    def test_stops_at_the_totals_footer(self):
        """The Investment/Current Value footer row must not become a holding."""
        assert all("Investment" not in h.raw_name for h in parse_dhan(DHAN).holdings)
        assert len(parse_dhan(DHAN).holdings) == 2

    def test_reads_the_embedded_download_timestamp(self):
        assert parse_dhan(DHAN).file_as_of == dt.datetime(2026, 8, 30, 19, 18)

    def test_pii_is_fingerprinted_never_retained(self):
        snap = parse_dhan(DHAN)
        blob = repr(snap)
        for secret in ("ABCD12345X", "9999999999", "someone@example.com", "SOMEONE"):
            assert secret not in blob, f"PII leaked: {secret}"
        assert snap.account_fingerprint
        assert len(snap.account_fingerprint) <= 16

    def test_fingerprint_is_stable_and_distinguishes_accounts(self):
        other = DHAN.replace("ABCD12345X", "WXYZ98765Q")
        assert parse_dhan(DHAN).account_fingerprint == parse_dhan(DHAN).account_fingerprint
        assert parse_dhan(DHAN).account_fingerprint != parse_dhan(other).account_fingerprint

    def test_dhan_rows_have_no_symbol_yet(self):
        """Dhan gives names; resolution is a separate, blocking step."""
        assert all(h.symbol is None for h in parse_dhan(DHAN).holdings)


class TestParseKite:
    def test_instrument_is_already_a_ticker(self):
        snap = parse_kite(KITE)
        assert [h.symbol for h in snap.holdings] == ["BAJAJ-AUTO", "DIVISLAB"]
        assert [h.quantity for h in snap.holdings] == [12.0, 30.0]

    def test_kite_has_no_embedded_timestamp(self):
        """Kite states no download time, so provenance is weaker than Dhan's and the
        caller must fall back to file mtime and label it differently."""
        assert parse_kite(KITE).file_as_of is None

    def test_no_account_identity_in_kite_export(self):
        assert parse_kite(KITE).account_fingerprint is None


class TestNormalizeName:
    @pytest.mark.parametrize("raw,expected", [
        ("DATA PATTERNS INDIA LTD", "DATA PATTERNS"),
        ("Ather Energy Limited", "ATHER ENERGY"),
        ("THE GE SHPG.LTD", "GE SHPG"),
        ("NETWEB TECH INDIA LTD", "NETWEB TECH"),
    ])
    def test_strips_corporate_suffixes_and_noise(self, raw, expected):
        assert normalize_name(raw) == expected

    def test_is_idempotent(self):
        once = normalize_name("Data Patterns India Ltd")
        assert normalize_name(once) == once


class TestReconcile:
    def test_matching_quantities_produce_no_gap(self):
        target = {"AAA": 10, "BBB": 5}
        observed = {"AAA": 10, "BBB": 5}
        r = reconcile(target, observed, discretionary={})
        assert r["gaps"] == []
        assert r["in_sync"] is True

    def test_short_position_becomes_a_buy(self):
        r = reconcile({"AAA": 10}, {"AAA": 4}, discretionary={})
        assert r["gaps"] == [{"symbol": "AAA", "action": "BUY", "quantity": 6,
                              "target": 10, "observed": 4}]

    def test_excess_becomes_a_sell(self):
        r = reconcile({"AAA": 4}, {"AAA": 10}, discretionary={})
        assert r["gaps"][0]["action"] == "SELL"
        assert r["gaps"][0]["quantity"] == 6

    def test_missing_symbol_is_a_full_buy(self):
        r = reconcile({"AAA": 7}, {}, discretionary={})
        assert r["gaps"] == [{"symbol": "AAA", "action": "BUY", "quantity": 7,
                              "target": 7, "observed": 0}]

    def test_unknown_broker_symbol_is_unclassified_not_a_sell(self):
        """A name the system never picked might be the user's own choice. It must be
        surfaced for a decision, never auto-queued for sale."""
        r = reconcile({}, {"GOLDBEES": 50}, discretionary={})
        assert r["gaps"] == []
        assert r["unclassified"] == [{"symbol": "GOLDBEES", "quantity": 50}]

    def test_discretionary_quantity_is_netted_out(self):
        r = reconcile({}, {"GOLDBEES": 50}, discretionary={"GOLDBEES": 50})
        assert r["gaps"] == []
        assert r["unclassified"] == []

    def test_discretionary_is_a_quantity_not_a_symbol_exclusion(self):
        """If the strategy later picks a symbol the user also holds privately, the
        arithmetic must still work: 50 held privately, 30 wanted by the system."""
        r = reconcile({"GOLDBEES": 30}, {"GOLDBEES": 50}, discretionary={"GOLDBEES": 50})
        assert r["gaps"] == [{"symbol": "GOLDBEES", "action": "BUY", "quantity": 30,
                              "target": 30, "observed": 0}]

    def test_partial_discretionary(self):
        r = reconcile({}, {"GOLDBEES": 50}, discretionary={"GOLDBEES": 20})
        assert r["unclassified"] == [{"symbol": "GOLDBEES", "quantity": 30}]

    def test_gaps_are_ordered_biggest_first(self):
        r = reconcile({"AAA": 100, "BBB": 3}, {}, discretionary={})
        assert [g["symbol"] for g in r["gaps"]] == ["AAA", "BBB"]
