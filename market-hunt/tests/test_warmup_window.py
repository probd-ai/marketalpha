"""Warm-up window: the first rebalance must rank on real lookback history.

Bug (engine defect #3): prices/indicators were loaded only from start_date, so on the first
rebalance every stock's price window held one bar, calculate_stock_momentum_fast returned 0.0 for
all, and the stable sort kept the qualified names in ALPHABETICAL order — the "top 7 by 20-day
return" on day one was the first 7 names A..Z (LargeCaps 2022: ABFRL, ADANIENT, ASTRAL, ATGL...).

Contract:
  * data is loaded from warmup_start(start_date), far enough back for the longest lookback;
  * the simulation still TRADES only on sessions on/after start_date (simulation_dates);
  * with warm-up history, day-one momentum is the real return, so ranking is by return.
"""
from datetime import datetime, timedelta

from engine.stateless import (WARMUP_CALENDAR_DAYS, build_price_series, calculate_stock_momentum_fast,
                              simulation_dates, warmup_start)


def _hist(start: datetime, n: int, px) -> dict:
    """date_str -> {symbol: {close_price}} for n consecutive calendar days."""
    out = {}
    for i in range(n):
        ds = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        out[ds] = {sym: {"close_price": f(i)} for sym, f in px.items()}
    return out


# AAA sorts first alphabetically but FALLS; ZZZ sorts last but RISES — the ranking must pick ZZZ.
PX = {"AAA": lambda i: 200.0 * 0.995 ** i, "ZZZ": lambda i: 100.0 * 1.005 ** i}
START = datetime(2022, 1, 3)


class TestWarmupStart:
    def test_reaches_back_past_the_longest_lookback(self):
        # price_roc_222d looks back 300 calendar days; warm-up must cover it
        assert WARMUP_CALENDAR_DAYS >= 300
        assert warmup_start(START) == START - timedelta(days=WARMUP_CALENDAR_DAYS)

    def test_accepts_a_date_string(self):
        assert warmup_start("2022-01-03") == START - timedelta(days=WARMUP_CALENDAR_DAYS)


class TestSimulationDates:
    def test_trades_only_from_start_even_when_warmup_is_loaded(self):
        ind = {"2021-12-30": {}, "2021-12-31": {}, "2022-01-03": {}, "2022-01-04": {}}
        px = {"2021-12-30": {}, "2021-12-31": {}, "2022-01-03": {}, "2022-01-04": {}}
        assert simulation_dates(ind, px, "2022-01-03") == ["2022-01-03", "2022-01-04"]

    def test_needs_both_indicator_and_price(self):
        ind = {"2022-01-03": {}, "2022-01-05": {}}
        px = {"2022-01-03": {}, "2022-01-04": {}, "2022-01-05": {}}
        assert simulation_dates(ind, px, "2022-01-01") == ["2022-01-03", "2022-01-05"]

    def test_end_date_bound(self):
        ind = {d: {} for d in ["2022-01-03", "2022-01-04", "2022-01-05"]}
        assert simulation_dates(ind, ind, "2022-01-03", "2022-01-04") == ["2022-01-03", "2022-01-04"]


class TestDayOneRanking:
    def _scores(self, hist):
        series = build_price_series(hist)
        return {s: calculate_stock_momentum_fast(series.get(s), None, START, "20_day_return") for s in PX}

    def test_without_warmup_every_score_is_zero(self):
        """The bug, pinned: one bar in the window -> 0.0 for all -> alphabetical ties."""
        scores = self._scores(_hist(START, 1, PX))
        assert scores == {"AAA": 0.0, "ZZZ": 0.0}

    def test_with_warmup_day_one_ranks_by_real_return(self):
        ws = warmup_start(START)
        scores = self._scores(_hist(ws, (START - ws).days + 1, PX))
        assert scores["ZZZ"] > 0 > scores["AAA"]
        ranked = sorted(PX, key=lambda s: scores[s], reverse=True)
        assert ranked == ["ZZZ", "AAA"]          # by return, not by name
