"""Primary filter: an ATH/ATL band (plus optional top-50% rank cut) applied between
the strategy filter and the momentum ranking.

Contract (docs/specs/primary-filter-ath-atl.md):
  - take="none" skips the stage entirely — the pool passes through untouched.
  - ATH keeps names whose close is within `band`% BELOW their all-time-high close;
    ATL keeps names within `band`% ABOVE their all-time-low close.
  - take="top50" then keeps ceil(n/2) — the half nearest the extreme.
  - A name with no price, or no extreme yet, is dropped: it cannot be ranked honestly.
  - Extremes are point-in-time: the running max/min of close up to (and including)
    the current date, seeded from history before the simulation window.
"""
from datetime import datetime

import pytest

from engine.stateless import (
    apply_primary_filter,
    build_extreme_series,
    fetch_pre_window_extremes,
)


def _stocks(*symbols):
    return [{"symbol": s, "truevx_score": 50} for s in symbols]


def _syms(stocks):
    return [s["symbol"] for s in stocks]


def _px(**closes):
    return {s: {"close_price": c} for s, c in closes.items()}


# ── take="none" is the master switch ─────────────────────────────────────────

class TestMasterSwitch:
    def test_take_none_passes_pool_through_untouched(self):
        pool = _stocks("A", "B", "C")
        out = apply_primary_filter(pool, _px(A=1, B=1, C=1),
                                   {"A": (100, 1), "B": (100, 1), "C": (100, 1)},
                                   metric="ath", band_percent=0.0, take="none")
        assert out is pool, "take='none' must not even copy the pool"

    def test_unknown_take_is_rejected(self):
        with pytest.raises(ValueError, match="take"):
            apply_primary_filter(_stocks("A"), _px(A=100), {"A": (100, 50)},
                                 metric="ath", band_percent=5.0, take="half")

    def test_unknown_metric_is_rejected(self):
        with pytest.raises(ValueError, match="metric"):
            apply_primary_filter(_stocks("A"), _px(A=100), {"A": (100, 50)},
                                 metric="ma200", band_percent=5.0, take="all")


# ── ATH band ─────────────────────────────────────────────────────────────────

class TestAthBand:
    def test_at_the_high_always_passes(self):
        out = apply_primary_filter(_stocks("A"), _px(A=100.0), {"A": (100.0, 10.0)},
                                   metric="ath", band_percent=0.0, take="all")
        assert _syms(out) == ["A"]

    def test_exact_band_edge_passes(self):
        """5% band: close/ATH == 0.95 exactly must pass — the band is inclusive."""
        out = apply_primary_filter(_stocks("A"), _px(A=95.0), {"A": (100.0, 10.0)},
                                   metric="ath", band_percent=5.0, take="all")
        assert _syms(out) == ["A"]

    def test_just_outside_band_is_dropped(self):
        out = apply_primary_filter(_stocks("A"), _px(A=94.99), {"A": (100.0, 10.0)},
                                   metric="ath", band_percent=5.0, take="all")
        assert out == []

    def test_band_splits_a_mixed_pool(self):
        stocks = _stocks("NEAR", "EDGE", "FAR")
        prices = _px(NEAR=99.0, EDGE=95.0, FAR=80.0)
        ext = {"NEAR": (100.0, 1.0), "EDGE": (100.0, 1.0), "FAR": (100.0, 1.0)}
        out = apply_primary_filter(stocks, prices, ext, metric="ath",
                                   band_percent=5.0, take="all")
        assert sorted(_syms(out)) == ["EDGE", "NEAR"]

    def test_zero_band_demands_a_fresh_high(self):
        stocks = _stocks("HIGH", "NEAR")
        prices = _px(HIGH=100.0, NEAR=99.99)
        ext = {"HIGH": (100.0, 1.0), "NEAR": (100.0, 1.0)}
        out = apply_primary_filter(stocks, prices, ext, metric="ath",
                                   band_percent=0.0, take="all")
        assert _syms(out) == ["HIGH"]


# ── ATL band ─────────────────────────────────────────────────────────────────

class TestAtlBand:
    def test_at_the_low_always_passes(self):
        out = apply_primary_filter(_stocks("A"), _px(A=10.0), {"A": (100.0, 10.0)},
                                   metric="atl", band_percent=0.0, take="all")
        assert _syms(out) == ["A"]

    def test_exact_band_edge_passes(self):
        """5% band: close/ATL == 1.05 exactly must pass."""
        out = apply_primary_filter(_stocks("A"), _px(A=10.5), {"A": (100.0, 10.0)},
                                   metric="atl", band_percent=5.0, take="all")
        assert _syms(out) == ["A"]

    def test_just_outside_band_is_dropped(self):
        out = apply_primary_filter(_stocks("A"), _px(A=10.51), {"A": (100.0, 10.0)},
                                   metric="atl", band_percent=5.0, take="all")
        assert out == []

    def test_atl_keeps_the_beaten_down_not_the_leaders(self):
        stocks = _stocks("CRUSHED", "LEADER")
        prices = _px(CRUSHED=10.2, LEADER=99.0)
        ext = {"CRUSHED": (100.0, 10.0), "LEADER": (100.0, 10.0)}
        out = apply_primary_filter(stocks, prices, ext, metric="atl",
                                   band_percent=5.0, take="all")
        assert _syms(out) == ["CRUSHED"]


# ── top50 rank cut ───────────────────────────────────────────────────────────

class TestTop50:
    def test_ath_top50_keeps_the_names_nearest_the_high(self):
        stocks = _stocks("A", "B", "C", "D")
        prices = _px(A=100.0, B=99.0, C=97.0, D=96.0)   # ratios 1.00 .99 .97 .96
        ext = {s: (100.0, 1.0) for s in "ABCD"}
        out = apply_primary_filter(stocks, prices, ext, metric="ath",
                                   band_percent=10.0, take="top50")
        assert _syms(out) == ["A", "B"]

    def test_atl_top50_keeps_the_names_nearest_the_low(self):
        stocks = _stocks("A", "B", "C", "D")
        prices = _px(A=10.0, B=10.1, C=10.5, D=10.9)    # ratios 1.00 1.01 1.05 1.09
        ext = {s: (100.0, 10.0) for s in "ABCD"}
        out = apply_primary_filter(stocks, prices, ext, metric="atl",
                                   band_percent=10.0, take="top50")
        assert _syms(out) == ["A", "B"]

    def test_odd_count_rounds_up_never_to_zero(self):
        """ceil(n/2): 3 -> 2, and a 1-name band must keep that one name."""
        ext = {s: (100.0, 1.0) for s in "ABC"}
        three = apply_primary_filter(_stocks("A", "B", "C"), _px(A=100.0, B=99.0, C=98.0),
                                     ext, metric="ath", band_percent=10.0, take="top50")
        assert _syms(three) == ["A", "B"]
        one = apply_primary_filter(_stocks("A"), _px(A=100.0), ext,
                                   metric="ath", band_percent=10.0, take="top50")
        assert _syms(one) == ["A"]

    def test_top50_cuts_only_what_the_band_left(self):
        """The rank cut is applied AFTER the band, not to the raw pool."""
        stocks = _stocks("A", "B", "FAR1", "FAR2")
        prices = _px(A=100.0, B=99.0, FAR1=50.0, FAR2=40.0)
        ext = {s: (100.0, 1.0) for s in ("A", "B", "FAR1", "FAR2")}
        out = apply_primary_filter(stocks, prices, ext, metric="ath",
                                   band_percent=5.0, take="top50")
        assert _syms(out) == ["A"], "band leaves A,B -> ceil(2/2)=1 -> A"

    def test_ties_break_on_symbol_for_reproducibility(self):
        stocks = _stocks("ZZ", "AA", "MM", "BB")
        prices = _px(ZZ=100.0, AA=100.0, MM=100.0, BB=100.0)
        ext = {s: (100.0, 1.0) for s in ("ZZ", "AA", "MM", "BB")}
        out = apply_primary_filter(stocks, prices, ext, metric="ath",
                                   band_percent=1.0, take="top50")
        assert _syms(out) == ["AA", "BB"]


# ── unrankable names are dropped, never scored ───────────────────────────────

class TestUnrankable:
    def test_missing_price_is_dropped(self):
        out = apply_primary_filter(_stocks("A", "B"), _px(A=100.0),
                                   {"A": (100.0, 1.0), "B": (100.0, 1.0)},
                                   metric="ath", band_percent=50.0, take="all")
        assert _syms(out) == ["A"]

    def test_missing_extreme_is_dropped(self):
        out = apply_primary_filter(_stocks("A", "B"), _px(A=100.0, B=100.0),
                                   {"A": (100.0, 1.0)},
                                   metric="ath", band_percent=50.0, take="all")
        assert _syms(out) == ["A"]

    def test_non_positive_extreme_is_dropped_not_divided_by(self):
        out = apply_primary_filter(_stocks("A"), _px(A=100.0), {"A": (0.0, 0.0)},
                                   metric="ath", band_percent=50.0, take="all")
        assert out == []

    def test_empty_pool_stays_empty(self):
        assert apply_primary_filter([], {}, {}, metric="ath",
                                    band_percent=5.0, take="top50") == []


# ── running extremes are point-in-time ───────────────────────────────────────

class TestExtremeSeries:
    def test_running_max_and_min_never_see_the_future(self):
        price_data = {
            "2024-01-01": _px(A=100.0),
            "2024-01-02": _px(A=120.0),
            "2024-01-03": _px(A=90.0),
            "2024-01-04": _px(A=150.0),
        }
        ser = build_extreme_series(price_data, seed={})
        assert ser["2024-01-01"]["A"] == (100.0, 100.0)
        assert ser["2024-01-02"]["A"] == (120.0, 100.0)
        assert ser["2024-01-03"]["A"] == (120.0, 90.0)
        assert ser["2024-01-04"]["A"] == (150.0, 90.0)

    def test_pre_window_seed_sets_the_all_time_extreme(self):
        """History before start_date is what makes it ALL-time, not window-time."""
        price_data = {"2024-01-01": _px(A=100.0), "2024-01-02": _px(A=110.0)}
        ser = build_extreme_series(price_data, seed={"A": (500.0, 20.0)})
        assert ser["2024-01-01"]["A"] == (500.0, 20.0)
        assert ser["2024-01-02"]["A"] == (500.0, 20.0)

    def test_in_window_high_can_exceed_the_seed(self):
        price_data = {"2024-01-01": _px(A=600.0)}
        ser = build_extreme_series(price_data, seed={"A": (500.0, 20.0)})
        assert ser["2024-01-01"]["A"] == (600.0, 20.0)

    def test_symbol_listed_after_start_has_no_seed_and_starts_clean(self):
        price_data = {
            "2024-01-01": _px(OLD=100.0),
            "2024-01-02": {"OLD": {"close_price": 100.0}, "IPO": {"close_price": 50.0}},
        }
        ser = build_extreme_series(price_data, seed={"OLD": (900.0, 10.0)})
        assert "IPO" not in ser["2024-01-01"]
        assert ser["2024-01-02"]["IPO"] == (50.0, 50.0)

    def test_a_gap_day_carries_the_extreme_forward(self):
        """A symbol absent on a date keeps the extreme it had — it is not reset."""
        price_data = {"2024-01-01": _px(A=100.0),
                      "2024-01-02": _px(B=10.0),
                      "2024-01-03": _px(A=50.0)}
        ser = build_extreme_series(price_data, seed={})
        assert ser["2024-01-02"]["A"] == (100.0, 100.0)
        assert ser["2024-01-03"]["A"] == (100.0, 50.0)

    def test_dates_are_processed_in_calendar_order_not_dict_order(self):
        price_data = {"2024-01-03": _px(A=50.0),
                      "2024-01-01": _px(A=100.0),
                      "2024-01-02": _px(A=200.0)}
        ser = build_extreme_series(price_data, seed={})
        assert ser["2024-01-01"]["A"] == (100.0, 100.0)
        assert ser["2024-01-03"]["A"] == (200.0, 50.0)


# ── momentum_ranking="none" must never silently rank ─────────────────────────

class TestNoneRankingGuard:
    @pytest.mark.asyncio
    async def test_none_with_a_cap_is_a_loud_error(self):
        """All-zero scores would yield an ALPHABETICAL top-N that looks plausible."""
        from engine.stateless import select_top_stocks_by_momentum
        with pytest.raises(ValueError, match="none"):
            await select_top_stocks_by_momentum(
                qualified_stocks=_stocks("A", "B", "C"), current_holdings={},
                price_data_history={}, indicator_data_history={},
                current_date="2024-01-02", max_holdings=2, momentum_method="none")

    @pytest.mark.asyncio
    async def test_none_without_a_cap_holds_everything(self):
        from engine.stateless import select_top_stocks_by_momentum
        sel, added, removed = await select_top_stocks_by_momentum(
            qualified_stocks=_stocks("A", "B", "C"), current_holdings={},
            price_data_history={}, indicator_data_history={},
            current_date="2024-01-02", max_holdings=None, momentum_method="none")
        assert sorted(_syms(sel)) == ["A", "B", "C"]
        assert sorted(added) == ["A", "B", "C"]
        assert removed == []

    @pytest.mark.asyncio
    async def test_uncapped_path_returns_symbols_not_stock_dicts(self):
        """`added` must be SYMBOLS on every path: the day result's "new_added"
        feeds the UI's New Additions panel, which renders each entry as a string."""
        from engine.stateless import select_top_stocks_by_momentum
        _, added, _ = await select_top_stocks_by_momentum(
            qualified_stocks=_stocks("A", "B"), current_holdings={},
            price_data_history={}, indicator_data_history={},
            current_date="2024-01-02", max_holdings=None,
            momentum_method="20_day_return")
        assert all(isinstance(a, str) for a in added), added


# ── pre-window seed: one $group per partition, merged across partitions ──────

class _FakeColl:
    def __init__(self, rows):
        self._rows = rows
        self.seen_pipelines = []

    def aggregate(self, pipeline):
        self.seen_pipelines.append(pipeline)
        cutoff = pipeline[0]["$match"]["date"]["$lt"]
        wanted = set(pipeline[0]["$match"]["symbol"]["$in"])
        grouped = {}
        for sym, dt, close in self._rows:
            if sym not in wanted or dt >= cutoff:
                continue
            hi, lo = grouped.get(sym, (close, close))
            grouped[sym] = (max(hi, close), min(lo, close))
        return [{"_id": s, "hi": hi, "lo": lo} for s, (hi, lo) in grouped.items()]


class _FakeDb:
    def __init__(self, colls):
        self._colls = colls

    def list_collection_names(self):
        return list(self._colls) + ["indicators", "index_meta"]

    def __getitem__(self, name):
        return self._colls[name]


class TestPreWindowSeed:
    def _db(self):
        return _FakeDb({
            "prices_2015_2019": _FakeColl([
                ("A", datetime(2016, 5, 1), 40.0),
                ("A", datetime(2018, 5, 1), 900.0),   # the all-time high, 6y before the run
                ("B", datetime(2017, 1, 1), 55.0),
            ]),
            "prices_2020_2024": _FakeColl([
                ("A", datetime(2021, 1, 1), 300.0),
                ("A", datetime(2024, 6, 1), 20.0),    # the all-time low
                ("A", datetime(2024, 12, 1), 5.0),    # AFTER the cutoff — must be excluded
            ]),
        })

    def test_extremes_merge_across_partitions(self):
        seed = fetch_pre_window_extremes(self._db(), ["A", "B"], datetime(2024, 7, 1))
        assert seed["A"] == (900.0, 20.0)
        assert seed["B"] == (55.0, 55.0)

    def test_bars_after_the_cutoff_are_excluded(self):
        """The cutoff is what keeps the seed point-in-time — 5.0 is a later bar."""
        seed = fetch_pre_window_extremes(self._db(), ["A"], datetime(2024, 7, 1))
        assert seed["A"][1] == 20.0, "a post-cutoff bar leaked into the all-time low"

    def test_only_requested_symbols_come_back(self):
        seed = fetch_pre_window_extremes(self._db(), ["B"], datetime(2024, 7, 1))
        assert set(seed) == {"B"}

    def test_no_symbols_makes_no_query(self):
        db = self._db()
        assert fetch_pre_window_extremes(db, [], datetime(2024, 7, 1)) == {}
        assert db["prices_2015_2019"].seen_pipelines == []

    def test_non_price_collections_are_not_scanned(self):
        db = self._db()
        fetch_pre_window_extremes(db, ["A"], datetime(2024, 7, 1))
        assert all(c.seen_pipelines for c in db._colls.values())

    def test_seed_feeds_straight_into_the_running_series(self):
        """End-to-end: a 6-year-old high still gates a name inside the window."""
        seed = fetch_pre_window_extremes(self._db(), ["A"], datetime(2024, 7, 1))
        ser = build_extreme_series({"2024-07-01": {"A": {"close_price": 500.0}}}, seed)
        out = apply_primary_filter(_stocks("A"), {"A": {"close_price": 500.0}},
                                   ser["2024-07-01"], metric="ath",
                                   band_percent=5.0, take="all")
        assert out == [], "500 is 44% below the 900 all-time high — must not pass a 5% band"


# ── bottom50: the half FURTHEST from the extreme ─────────────────────────────

class TestBottom50:
    def test_ath_bottom50_keeps_the_names_furthest_below_the_high(self):
        stocks = _stocks("A", "B", "C", "D")
        prices = _px(A=100.0, B=99.0, C=97.0, D=96.0)   # ratios 1.00 .99 .97 .96
        ext = {s: (100.0, 1.0) for s in "ABCD"}
        out = apply_primary_filter(stocks, prices, ext, metric="ath",
                                   band_percent=10.0, take="bottom50")
        assert _syms(out) == ["D", "C"]

    def test_atl_bottom50_keeps_the_names_furthest_above_the_low(self):
        stocks = _stocks("A", "B", "C", "D")
        prices = _px(A=10.0, B=10.1, C=10.5, D=10.9)    # ratios 1.00 1.01 1.05 1.09
        ext = {s: (100.0, 10.0) for s in "ABCD"}
        out = apply_primary_filter(stocks, prices, ext, metric="atl",
                                   band_percent=10.0, take="bottom50")
        assert _syms(out) == ["D", "C"]

    def test_bottom50_is_the_exact_mirror_of_top50_on_an_even_band(self):
        stocks = _stocks("A", "B", "C", "D")
        prices = _px(A=100.0, B=99.0, C=97.0, D=96.0)
        ext = {s: (100.0, 1.0) for s in "ABCD"}
        kw = dict(metric="ath", band_percent=10.0)
        top = set(_syms(apply_primary_filter(stocks, prices, ext, take="top50", **kw)))
        bot = set(_syms(apply_primary_filter(stocks, prices, ext, take="bottom50", **kw)))
        allb = set(_syms(apply_primary_filter(stocks, prices, ext, take="all", **kw)))
        assert top | bot == allb and not (top & bot)

    def test_odd_band_shares_the_median_name(self):
        """Both halves use ceil(n/2), so 3 -> 2 and 2 -> 2 overlap on the middle name."""
        stocks = _stocks("A", "B", "C")
        prices = _px(A=100.0, B=99.0, C=98.0)
        ext = {s: (100.0, 1.0) for s in "ABC"}
        kw = dict(metric="ath", band_percent=10.0)
        top = _syms(apply_primary_filter(stocks, prices, ext, take="top50", **kw))
        bot = _syms(apply_primary_filter(stocks, prices, ext, take="bottom50", **kw))
        assert top == ["A", "B"] and bot == ["C", "B"]

    def test_bottom50_still_respects_the_band(self):
        """The rank cut applies AFTER the band — a name outside it never returns."""
        stocks = _stocks("A", "B", "FAR")
        prices = _px(A=100.0, B=99.0, FAR=50.0)
        ext = {s: (100.0, 1.0) for s in ("A", "B", "FAR")}
        out = apply_primary_filter(stocks, prices, ext, metric="ath",
                                   band_percent=5.0, take="bottom50")
        assert _syms(out) == ["B"], "FAR is outside the 5% band and must not come back"

    def test_single_name_band_is_never_emptied(self):
        out = apply_primary_filter(_stocks("A"), _px(A=100.0), {"A": (100.0, 1.0)},
                                   metric="ath", band_percent=10.0, take="bottom50")
        assert _syms(out) == ["A"]
