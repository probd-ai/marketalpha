"""Corporate-action aware price refresh.

A split or bonus re-prices a stock's ENTIRE history at the source. Our incremental
refresh only ever rewrites a ~5-day buffer, so it appends adjusted bars onto
unadjusted history and manufactures a phantom crash — KIRLPNU showed −48.4% on
2026-08-10 from a 1:2 split, which drove `truevx_score` from 50.0 to 0.0 and would
have forced a sale on a move that never happened.

Two independent guards are tested here:
  * `filter_price_affecting` — the NSE corporate-actions feed, primary detector
  * `detect_price_discontinuities` — a price-jump backstop for whatever the feed
    misses (it is unauthenticated, rate-limited, and only covers a short window)
"""
import datetime as dt

from nse_data_client import PRICE_AFFECTING_ACTIONS, filter_price_affecting
from scripts.refresh_all import (
    PRICE_JUMP_RATIO,
    detect_price_discontinuities,
    split_by_corporate_action,
)


class TestFilterPriceAffecting:
    def test_keeps_eq_split(self):
        rows = [{"symbol": "KIRLPNU", "series": "EQ",
                 "subject": "Face Value Split (Sub-Division) - From Rs 10/- to Rs 5/-",
                 "exDate": "10-Aug-2026", "comp": "Kirloskar Pneumatic"}]
        out = filter_price_affecting(rows)
        assert [r["symbol"] for r in out] == ["KIRLPNU"]
        assert out[0]["exDate"] == "10-Aug-2026"

    def test_drops_non_eq_series(self):
        rows = [{"symbol": "SMECO", "series": "SM", "subject": "Bonus 1:1"}]
        assert filter_price_affecting(rows) == []

    def test_matching_is_case_insensitive(self):
        rows = [{"symbol": "X", "series": "EQ", "subject": "BONUS ISSUE 2:1"}]
        assert len(filter_price_affecting(rows)) == 1

    def test_ignores_dividends(self):
        """Dividends are not adjusted by the price source, so they must not trigger
        a full re-download of every dividend-paying stock every quarter."""
        rows = [{"symbol": "ITC", "series": "EQ", "subject": "Interim Dividend Rs 6.25"}]
        assert filter_price_affecting(rows) == []

    def test_each_price_affecting_keyword_matches(self):
        for kw in PRICE_AFFECTING_ACTIONS:
            rows = [{"symbol": "S", "series": "EQ", "subject": f"Scheme of {kw} approved"}]
            assert len(filter_price_affecting(rows)) == 1, kw

    def test_dedups_repeated_symbol(self):
        rows = [{"symbol": "A", "series": "EQ", "subject": "Bonus 1:1"},
                {"symbol": "A", "series": "EQ", "subject": "Stock Split 1:2"}]
        assert len(filter_price_affecting(rows)) == 1

    def test_tolerates_empty_and_missing_fields(self):
        assert filter_price_affecting(None) == []
        assert filter_price_affecting([]) == []
        assert filter_price_affecting([{}]) == []
        assert filter_price_affecting([{"symbol": "", "series": "EQ",
                                        "subject": "Bonus"}]) == []


class TestSplitByCorporateAction:
    def test_affected_go_full_rest_incremental(self):
        syms = ["AAA", "KIRLPNU", "BBB", "TDPOWERSYS"]
        actions = [{"symbol": "KIRLPNU"}, {"symbol": "TDPOWERSYS"}]
        full, inc = split_by_corporate_action(syms, actions)
        assert full == ["KIRLPNU", "TDPOWERSYS"]
        assert inc == ["AAA", "BBB"]

    def test_actions_outside_the_universe_are_ignored(self):
        full, inc = split_by_corporate_action(["AAA"], [{"symbol": "ZZZ"}])
        assert full == []
        assert inc == ["AAA"]

    def test_no_actions_means_everything_incremental(self):
        full, inc = split_by_corporate_action(["AAA", "BBB"], [])
        assert full == []
        assert inc == ["AAA", "BBB"]

    def test_input_order_is_preserved(self):
        syms = ["D", "C", "B", "A"]
        full, inc = split_by_corporate_action(syms, [{"symbol": "C"}, {"symbol": "A"}])
        assert full == ["C", "A"]
        assert inc == ["D", "B"]


class TestDetectPriceDiscontinuities:
    def _bars(self, closes, start=dt.date(2026, 8, 3)):
        return [(start + dt.timedelta(days=i), c) for i, c in enumerate(closes)]

    def test_detects_the_kirlpnu_split(self):
        hits = detect_price_discontinuities(self._bars([1455.2, 1439.9, 743.3, 746.0]))
        assert len(hits) == 1
        assert hits[0]["prev_close"] == 1439.9
        assert hits[0]["close"] == 743.3
        # 1.94 not 2.00 — the stock also traded up ~3% on the ex-date, so the observed
        # ratio never lands exactly on the action's ratio.
        assert 1.9 < hits[0]["ratio"] < 2.0

    def test_flags_a_real_crash_too_which_is_why_it_only_reports(self):
        """INDUSINDBK fell 27% on 2025-03-11 on an accounting disclosure — a genuine
        move. It IS flagged, because real crashes and bonuses overlap in magnitude
        (IEX's 3:2 bonus and a real India VIX move are both ratio 1.42). No threshold
        separates them, so this scan must never auto-trigger a re-download: it would
        re-download INDUSINDBK on every run forever. The NSE feed is the only
        auto-trigger; this is a report-only backstop for a human to review.
        """
        hits = detect_price_discontinuities(self._bars([900.5, 655.95, 660.0]))
        assert len(hits) == 1
        assert 1.3 < hits[0]["ratio"] < 1.4

    def test_ignores_ordinary_moves(self):
        hits = detect_price_discontinuities(self._bars([100.0, 92.0, 108.0, 101.0]))
        assert hits == []

    def test_detects_reverse_split_upward_jump(self):
        hits = detect_price_discontinuities(self._bars([14.65, 145.12]))
        assert len(hits) == 1
        assert hits[0]["ratio"] < 1        # ratio = prev/close, <1 means price rose

    def test_threshold_boundary(self):
        """At the threshold it flags; just inside it does not — and anything larger
        must still flag, so the rule is one-sided in the right direction."""
        at = 100.0 / PRICE_JUMP_RATIO
        assert detect_price_discontinuities(self._bars([100.0, at]))
        assert detect_price_discontinuities(self._bars([100.0, at * 0.5]))
        assert detect_price_discontinuities(self._bars([100.0, at + 1.0])) == []

    def test_needs_at_least_two_bars(self):
        assert detect_price_discontinuities([]) == []
        assert detect_price_discontinuities(self._bars([100.0])) == []

    def test_skips_missing_and_nonpositive_closes(self):
        bars = [(dt.date(2026, 8, 3), 100.0), (dt.date(2026, 8, 4), None),
                (dt.date(2026, 8, 5), 0.0), (dt.date(2026, 8, 6), 98.0)]
        assert detect_price_discontinuities(bars) == []

    def test_reports_the_date_of_the_jump(self):
        hits = detect_price_discontinuities(self._bars([1439.9, 743.3]))
        assert hits[0]["date"] == dt.date(2026, 8, 4)
