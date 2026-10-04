"""Unit tests for the pure logic in scripts/refresh_all.py."""
import datetime as dt

import pytest

from scripts.refresh_all import (
    check_recon_consistency,
    classify_price_failures,
    STAGES,
    freshness_summary,
    most_recent_trading_day,
    parse_pr_page,
    pick_stages,
    relevant_prs,
    uncaptured_prs,
)


class TestPickStages:
    def test_default_is_all_stages(self):
        assert pick_stages(None, None) == STAGES

    def test_only_subset_preserves_order(self):
        assert pick_stages("recon,prices", None) == ["recon", "prices"]

    def test_skip_removes(self):
        assert pick_stages(None, "urls,indicators") == ["calendar", "mappings", "prices", "recon", "report"]

    def test_unknown_stage_rejected(self):
        with pytest.raises(SystemExit):
            pick_stages("prices,bogus", None)


class TestMostRecentTradingDay:
    def test_weekday_is_itself(self):
        assert most_recent_trading_day(dt.date(2026, 7, 3)) == dt.date(2026, 7, 3)  # Friday

    def test_saturday_rolls_to_friday(self):
        assert most_recent_trading_day(dt.date(2026, 7, 4)) == dt.date(2026, 7, 3)

    def test_sunday_rolls_to_friday(self):
        assert most_recent_trading_day(dt.date(2026, 7, 5)) == dt.date(2026, 7, 3)

    def test_holiday_rolls_back_past_it(self):
        # Sun 2026-10-04 after Gandhi Jayanti (Fri 10-02): the last session was Thu 10-01
        assert most_recent_trading_day(dt.date(2026, 10, 4), {dt.date(2026, 10, 2)}) == dt.date(2026, 10, 1)


PR_HTML = """
<div class="pressItem"><p>Jul 02, 2026</p>
<a href='/Press_Release/ind_prs02072026.pdf' target="_blank">Inclusions in Nifty IPO w.e.f. July 07, 2026</a></div>
<a href="/Press_Release/ind_prs04052026.pdf">Replacement in indices w.e.f. May 12, 2026</a>
<a href='/Press_Release/ind_prs06052026.pdf'>Changes in Nifty Fixed Income indices w.e.f. May 11, 2026</a>
<a href='/Press_Release/ind_prs04052026.pdf'>Replacement in indices w.e.f. May 12, 2026</a>
<a href='/Press_Release/ind_prs23042026.pdf'>Corporate Adjustment for Vedanta Ltd. and Replacement in Nifty Indices w.e.f. April 30, 2026</a>
"""


class TestParsePrPage:
    def test_parses_dates_links_titles_and_dedups(self):
        prs = parse_pr_page(PR_HTML)
        links = [p[1] for p in prs]
        assert links.count("Press_Release/ind_prs04052026.pdf") == 1
        assert (dt.date(2026, 5, 4), "Press_Release/ind_prs04052026.pdf",
                "Replacement in indices w.e.f. May 12, 2026") in prs

    def test_handles_both_quote_styles(self):
        prs = parse_pr_page(PR_HTML)
        assert any("ind_prs02072026" in p[1] for p in prs)  # single-quoted href


class TestRelevantPrs:
    def test_filters_fixed_income_and_ipo_noise(self):
        prs = parse_pr_page(PR_HTML)
        rel = relevant_prs(prs, dt.date(2026, 4, 1))
        names = {p[1].rsplit("/", 1)[-1] for p in rel}
        assert "ind_prs04052026.pdf" in names
        assert "ind_prs23042026.pdf" in names          # corporate adjustment
        assert "ind_prs06052026.pdf" not in names      # fixed income
        assert "ind_prs02072026.pdf" not in names      # Nifty IPO index churn

    def test_since_cutoff(self):
        prs = parse_pr_page(PR_HTML)
        assert relevant_prs(prs, dt.date(2026, 6, 1)) == []


class TestUncapturedPrs:
    def test_matches_on_pdf_basename_regardless_of_host(self):
        cands = [(dt.date(2026, 5, 4), "Press_Release/ind_prs04052026.pdf", "t")]
        stored = {"https://www.niftyindices.com/Press_Release/ind_prs04052026.pdf"}
        assert uncaptured_prs(cands, stored) == []

    def test_unstored_pr_is_flagged(self):
        cands = [(dt.date(2026, 5, 8), "Press_Release/ind_prs08052026.pdf", "t")]
        assert uncaptured_prs(cands, set()) == cands

    def test_reviewed_no_impact_pr_is_suppressed(self):
        cands = [(dt.date(2026, 6, 10), "Press_Release/ind_prs10062026.pdf", "t")]
        assert uncaptured_prs(cands, set(), {"ind_prs10062026.pdf"}) == []


class TestFreshnessSummary:
    def test_ok_within_tolerance(self):
        last = {"A": dt.date(2026, 7, 3), "B": dt.date(2026, 6, 19)}
        s = freshness_summary(last, dt.date(2026, 7, 3), tolerance=1)
        assert s["ok"] and s["stale"] == ["B"]

    def test_stale_beyond_tolerance(self):
        last = {"A": dt.date(2026, 6, 19), "B": dt.date(2026, 6, 19)}
        s = freshness_summary(last, dt.date(2026, 7, 3), tolerance=1)
        assert not s["ok"] and s["stale_count"] == 2


class TestClassifyPriceFailures:
    """A symbol blocked before the network must never look like a source gap.

    The 2026-08-01 refresh froze ~64 symbols because refresh-mappings nulled their
    scrip codes and the download path refused them outright. In the summary they
    were indistinguishable from delisted names, so the freeze read as normal
    attrition. Blocked symbols are a bug signature and must fail the stage.
    """

    def test_mapping_errors_are_separated_from_source_gaps(self):
        out = classify_price_failures({
            "LENSKART": "No NSE mapping found for symbol LENSKART",
            "AXISNIFTY": "No historical data fetched for AXISNIFTY",
        })
        assert out["blocked_by_mapping"] == ["LENSKART"]
        assert out["source_gaps"] == ["AXISNIFTY"]

    def test_blocked_symbols_fail_the_stage(self):
        assert classify_price_failures(
            {"LENSKART": "No NSE mapping found for symbol LENSKART"})["ok"] is False

    def test_source_gaps_alone_are_tolerated(self):
        assert classify_price_failures(
            {"AXISNIFTY": "No historical data fetched for AXISNIFTY"})["ok"] is True

    def test_no_failures_is_ok(self):
        out = classify_price_failures({})
        assert out["ok"] is True and out["blocked_by_mapping"] == []

    def test_unknown_errors_count_as_source_gaps(self):
        out = classify_price_failures({"FOO": "timed out"})
        assert out["source_gaps"] == ["FOO"] and out["ok"] is True


class TestCheckReconConsistency:
    """The recon gate compares stored rebalance events to today's constituents.

    niftyindices announces a review ~1 month before it takes effect, so a correctly
    captured event is routinely future-dated. Comparing one against the current
    constituent list flags it as 'added_but_absent' on every run until the effective
    date — a false alarm that trains the operator to ignore a critical gate.
    """
    TODAY = dt.date(2026, 8, 16)

    def _ev(self, day, added=(), removed=()):
        return {"effective_date": dt.datetime.combine(day, dt.time()),
                "added": list(added), "removed": list(removed)}

    def test_effective_event_matching_constituents_is_clean(self):
        out = check_recon_consistency(
            [self._ev(dt.date(2026, 6, 24), added=["BSE"], removed=["WIPRO"])],
            {"BSE"}, self.TODAY)
        assert out["gate_mismatch"] == {} and out["historical_gap"] == {}
        assert out["newest_effective"] == dt.date(2026, 6, 24)

    def test_announced_but_not_yet_effective_event_does_not_flag(self):
        out = check_recon_consistency(
            [self._ev(dt.date(2026, 9, 30), added=["BSE"], removed=["WIPRO"])],
            {"WIPRO"}, self.TODAY)
        assert out["gate_mismatch"] == {}
        assert out["historical_gap"] == {}
        assert out["newest_effective"] is None

    def test_future_event_does_not_mask_a_stale_effective_one(self):
        """The gate must still fire on the newest EFFECTIVE event."""
        out = check_recon_consistency(
            [self._ev(dt.date(2026, 6, 24), added=["FOO"], removed=["BAR"]),
             self._ev(dt.date(2026, 9, 30), added=["BSE"], removed=["WIPRO"])],
            {"BAR"}, self.TODAY)
        assert out["gate_mismatch"] == {"removed_but_present": ["BAR"],
                                        "added_but_absent": ["FOO"]}
        assert out["newest_effective"] == dt.date(2026, 6, 24)

    def test_cumulative_replay_reports_historical_gaps(self):
        out = check_recon_consistency(
            [self._ev(dt.date(2024, 3, 28), added=["ZOMATO"]),
             self._ev(dt.date(2026, 6, 24), added=["BSE"])],
            {"BSE"}, self.TODAY)
        assert out["gate_mismatch"] == {}
        assert out["historical_gap"]["added_but_absent"] == ["ZOMATO"]

    def test_no_events_is_not_a_mismatch(self):
        out = check_recon_consistency([], {"BSE"}, self.TODAY)
        assert out == {"gate_mismatch": {}, "historical_gap": {}, "newest_effective": None}
