from wave_models import WaveConfig
from wave_engine import run_wave_simulation

# ---------------------------------------------------------------------------
# Task 6 tests — monthly cadence, fractional exit, reentry, pre-gate,
#                max_holdings, cash invariant
# ---------------------------------------------------------------------------

def _members_as_of_patch_t6(monkeypatch, ever):
    import wave_engine
    monkeypatch.setattr(wave_engine, "members_as_of", lambda intervals, d: set(ever))


def test_monthly_cadence_only_buys_on_first_trading_day(monkeypatch):
    ever = ["AAA"]
    _members_as_of_patch_t6(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-15", "2020-02-03"]   # 2 months; firsts: 01-01, 02-03
    indicators = {"AAA": {ds: {"mean_long": 60, "mean_short": 10, "truevx_score": 10} for ds in dates}}
    prices = {"AAA": {ds: 100.0 for ds in dates}}
    wave = WaveConfig(
        capital_N=200000, tranches=20, reentry=True, buy_cadence="monthly", buy_date="first",
        exit_mode="full",
        entry=[[{"kind": "compare", "lhs_metric": "mean_long", "op": ">", "rhs": {"number": 50}}]],
        accumulate=[],
        exit=[[{"kind": "compare", "lhs_metric": "mean_long", "op": "<", "rhs": {"number": 0}}]],
    )
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave,
                              point_in_time=False)
    # one tranche each on 01-01 and 02-03, NOT on 01-15
    assert out["diagnostics"]["n_buys"] == 2


def test_fractional_exit_sells_one_tranche_at_a_time(monkeypatch):
    ever = ["AAA"]
    _members_as_of_patch_t6(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02", "2020-01-03"]
    indicators = {
        "AAA": {
            "2020-01-01": {"mean_long": 38},
            "2020-01-02": {"mean_long": 55},   # up-cross 40 & 50 → 2 tranches
            "2020-01-03": {"mean_long": 48},   # down-cross 50 → fractional exit fires
        }
    }
    prices = {"AAA": {ds: 100.0 for ds in dates}}
    wave = WaveConfig(
        capital_N=200000, tranches=20, reentry=True, buy_cadence="daily",
        exit_mode="fractional",
        entry=[
            [{"kind": "cross", "metric": "mean_long", "level": 40, "direction": "up"}],
            [{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "up"}],
        ],
        accumulate=[],
        exit=[[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "down"}]],
    )
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave,
                              point_in_time=False)
    # had 2 tranches; fractional exit fires once → 1 remains
    assert out["positions"]["AAA"]["tranches"] == 1


def test_reentry_false_bars_symbol_after_full_exit(monkeypatch):
    ever = ["AAA"]
    _members_as_of_patch_t6(monkeypatch, ever)
    # day0: seed state (mean_long=40, below entry threshold)
    # day1: up-cross 50 → entry
    # day2: down-cross 50 → full exit → symbol barred
    # day3: up-cross 50 again → should NOT re-enter (barred)
    dates = ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"]
    indicators = {
        "AAA": {
            "2020-01-01": {"mean_long": 40},   # seed: below 50, no cross
            "2020-01-02": {"mean_long": 55},   # up-cross 50 → entry
            "2020-01-03": {"mean_long": 45},   # down-cross 50 → full exit; barred
            "2020-01-04": {"mean_long": 55},   # up-cross 50 → barred, no re-entry
        }
    }
    prices = {"AAA": {ds: 100.0 for ds in dates}}
    wave = WaveConfig(
        capital_N=200000, tranches=20, reentry=False, buy_cadence="daily", exit_mode="full",
        entry=[[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "up"}]],
        accumulate=[],
        exit=[[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "down"}]],
    )
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave,
                              point_in_time=False)
    assert out["diagnostics"]["n_buys"] == 1   # only day2 buy; day4 barred
    assert out["diagnostics"]["n_exits"] == 1  # exited on day3
    assert out["positions"] == {}              # exited and never re-entered


def test_pregate_rules_filter_new_entries(monkeypatch):
    ever = ["AAA", "BBB"]
    _members_as_of_patch_t6(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02"]
    # day2: both would fire entry; BBB fails the pre-gate (truevx_score < 50)
    indicators = {
        "AAA": {
            "2020-01-01": {"mean_long": 45, "truevx_score": 70},
            "2020-01-02": {"mean_long": 55, "truevx_score": 70},
        },
        "BBB": {
            "2020-01-01": {"mean_long": 45, "truevx_score": 30},
            "2020-01-02": {"mean_long": 55, "truevx_score": 30},
        },
    }
    prices = {"AAA": {ds: 100.0 for ds in dates}, "BBB": {ds: 100.0 for ds in dates}}
    wave = WaveConfig(
        capital_N=200000, tranches=20, reentry=True, buy_cadence="daily", exit_mode="full",
        entry=[[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "up"}]],
        accumulate=[],
        exit=[[{"kind": "compare", "lhs_metric": "mean_long", "op": "<", "rhs": {"number": 0}}]],
    )
    rules = [{"metric": "truevx_score", "operator": ">=", "threshold": 50}]
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave, rules=rules,
                              point_in_time=False)
    # only AAA passes the gate; BBB's truevx_score=30 fails
    assert out["diagnostics"]["n_buys"] == 1
    assert "AAA" in out["positions"]
    assert "BBB" not in out["positions"]


def test_max_holdings_limits_new_entries(monkeypatch):
    ever = ["AAA", "BBB", "CCC"]
    _members_as_of_patch_t6(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02"]
    # day2: all 3 fire entry; max_holdings=2 → only 2 admitted
    indicators = {
        s: {
            "2020-01-01": {"mean_long": 45},
            "2020-01-02": {"mean_long": 55},
        }
        for s in ever
    }
    prices = {s: {ds: 100.0 for ds in dates} for s in ever}
    wave = WaveConfig(
        capital_N=300000, tranches=20, reentry=True, buy_cadence="daily", exit_mode="full",
        entry=[[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "up"}]],
        accumulate=[],
        exit=[[{"kind": "compare", "lhs_metric": "mean_long", "op": "<", "rhs": {"number": 0}}]],
    )
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave, max_holdings=2,
                              point_in_time=False)
    assert len(out["positions"]) == 2
    assert out["diagnostics"]["n_buys"] == 2


def test_cash_never_goes_negative(monkeypatch):
    """Invariant: cash >= 0 and total book value <= N0 at all times."""
    ever = ["AAA", "BBB"]
    _members_as_of_patch_t6(monkeypatch, ever)
    dates = [f"2020-01-{i:02d}" for i in range(1, 6)]
    indicators = {
        s: {ds: {"mean_long": 60} for ds in dates} for s in ever
    }
    prices = {s: {ds: 100.0 for ds in dates} for s in ever}
    wave = WaveConfig(
        capital_N=1000, tranches=3, reentry=True, buy_cadence="daily", exit_mode="full",
        entry=[[{"kind": "compare", "lhs_metric": "mean_long", "op": ">", "rhs": {"number": 50}}]],
        accumulate=[],
        exit=[[{"kind": "compare", "lhs_metric": "mean_long", "op": "<", "rhs": {"number": 0}}]],
    )
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave,
                              point_in_time=False)
    assert out["cash"] >= 0
    mv = sum(v["shares"] * 100.0 for v in out["positions"].values())
    assert out["cash"] + mv <= wave.capital_N + 1e-6  # within float tolerance


# ---------------------------------------------------------------------------
# Original Task 5 tests (must still pass)
# ---------------------------------------------------------------------------

def _members_as_of_patch(monkeypatch, ever):
    import wave_engine
    monkeypatch.setattr(wave_engine, "members_as_of", lambda intervals, d: set(ever))

def test_level_wave_buys_one_tranche_per_level_then_exits(monkeypatch):
    ever = ["AAA"]
    _members_as_of_patch(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02", "2020-01-03"]
    # day1 ml=38 (no pos), day2 ml=62 (up through 40/50/60 -> 3 tranches), day3 ml=72 (up through 70 -> exit)
    indicators = {"AAA": {"2020-01-01": {"mean_long": 38}, "2020-01-02": {"mean_long": 62},
                          "2020-01-03": {"mean_long": 72}}}
    prices = {"AAA": {"2020-01-01": 100.0, "2020-01-02": 100.0, "2020-01-03": 110.0}}
    g = lambda L: [{"kind": "cross", "metric": "mean_long", "level": L, "direction": "up"},
                   {"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up", "negate": True}]
    wave = WaveConfig(capital_N=300000, tranches=20, reentry=True, buy_cadence="daily", exit_mode="full",
                      entry=[g(40), g(50), g(60)], accumulate=[],
                      exit=[[{"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up"}],
                            [{"kind": "cross", "metric": "mean_long", "level": 40, "direction": "down"}]])
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave,
                              point_in_time=False)
    # 3 tranches of 15000 each bought day2 at 100 -> 450 shares, cash 300000-45000=255000
    assert out["diagnostics"]["n_buys"] == 3
    # day3 exit at 110: proceeds 450*110=49500; final equity = 255000 + 49500 = 304500
    assert round(out["equity_curve"][-1][1], 2) == 304500.00
    assert out["diagnostics"]["exit_takeprofit"] == 1
    assert out["positions"] == {}

def test_last_cross_dir_down_exit_classified_as_stop(monkeypatch):
    ever = ["AAA"]; _members_as_of_patch(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02", "2020-01-03"]
    # day2 up-cross 50 (entry via cross); day3 down-cross 50 sets last_cross_dir=down -> exit as stop
    indicators = {"AAA": {"2020-01-01": {"mean_long": 48}, "2020-01-02": {"mean_long": 55},
                          "2020-01-03": {"mean_long": 47}}}
    prices = {"AAA": {ds: 100.0 for ds in dates}}
    wave = WaveConfig(capital_N=200000, tranches=20, reentry=True, buy_cadence="daily", exit_mode="full",
                      entry=[[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "up"}]],
                      accumulate=[],
                      exit=[[{"kind": "last_cross_dir", "metric": "mean_long", "grid": [50, 60, 70], "direction": "down"}]])
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave,
                              point_in_time=False)
    assert out["diagnostics"]["n_exits"] == 1
    assert out["diagnostics"]["exit_stop"] == 1
    assert out["diagnostics"]["exit_takeprofit"] == 0
    assert out["positions"] == {}


# ---------------------------------------------------------------------------
# Fix 2 — _rank_top raises ValueError for unsupported momentum_method
# ---------------------------------------------------------------------------
# Unit-testing _rank_top directly is cleaner than wiring an unsupported
# momentum_method through a full run_wave_simulation call (which would require
# constructing a scenario where entrants > free slots AND plumbing a non-default
# momentum_method all the way through).  The ValueError is raised inside
# _rank_top before any scoring logic runs, so a direct call is the correct
# level of test.

def test_rank_top_raises_for_unsupported_momentum_method():
    from wave_engine import _rank_top
    prices = {"AAA": {"2020-01-02": 100.0}, "BBB": {"2020-01-02": 100.0}, "CCC": {"2020-01-02": 100.0}}
    import pytest
    with pytest.raises(ValueError, match="momentum_ranking='20_day_return'"):
        _rank_top(["AAA", "BBB", "CCC"], "2020-01-02", prices, {}, "truevx_roc", free=1)


# ---------------------------------------------------------------------------
# Fix 3 — fractional exit + reentry=False: fully-closed symbol is barred
# ---------------------------------------------------------------------------

def test_fractional_full_close_bars_reentry(monkeypatch):
    """With exit_mode='fractional' and reentry=False, once the last tranche
    sells (full close), the symbol must NOT re-enter even if a new entry signal
    fires later.

    Timeline:
      day1 (01-01): seed state — mean_long=38, no cross yet
      day2 (01-02): up-cross 50 → entry (1 tranche); mean_long=55
      day3 (01-03): down-cross 50 → fractional exit fires on a 1-tranche
                    position → full liquidation path (last tranche); symbol barred
      day4 (01-04): up-cross 50 again → barred, NO re-entry
    """
    ever = ["AAA"]
    _members_as_of_patch(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"]
    indicators = {
        "AAA": {
            "2020-01-01": {"mean_long": 38},
            "2020-01-02": {"mean_long": 55},   # up-cross 50 → entry
            "2020-01-03": {"mean_long": 45},   # down-cross 50 → fractional exit (1 tranche = full close)
            "2020-01-04": {"mean_long": 55},   # up-cross 50 again → barred
        }
    }
    prices = {"AAA": {ds: 100.0 for ds in dates}}
    wave = WaveConfig(
        capital_N=200000, tranches=20, reentry=False, buy_cadence="daily",
        exit_mode="fractional",
        entry=[[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "up"}]],
        accumulate=[],
        exit=[[{"kind": "cross", "metric": "mean_long", "level": 50, "direction": "down"}]],
    )
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave,
                              point_in_time=False)
    assert out["diagnostics"]["n_buys"] == 1   # only day2; day4 barred
    assert out["diagnostics"]["n_exits"] == 1  # full close on day3
    assert out["positions"] == {}              # closed and never re-entered


# ---------------------------------------------------------------------------
# Task 7 tests — costs, PIT fail-loud, summarize, results/diagnostics output
# ---------------------------------------------------------------------------

import pytest
from wave_engine import run_wave_simulation, summarize


def test_pit_fail_loud_when_no_intervals(monkeypatch):
    ever = ["AAA"]
    import wave_engine
    monkeypatch.setattr(wave_engine, "members_as_of", lambda intervals, d: set(ever))
    dates = ["2020-01-01"]
    indicators = {"AAA": {"2020-01-01": {"mean_long": 60}}}
    prices = {"AAA": {"2020-01-01": 100.0}}
    wave = WaveConfig(capital_N=100000, tranches=20, buy_cadence="daily",
                      entry=[[{"kind": "compare", "lhs_metric": "mean_long", "op": ">", "rhs": {"number": 50}}]],
                      exit=[[{"kind": "compare", "lhs_metric": "mean_long", "op": "<", "rhs": {"number": 0}}]])
    with pytest.raises(ValueError, match="point-in-time"):
        run_wave_simulation(ever, None, prices, indicators, {}, dates, wave, point_in_time=True)


def test_summary_metrics_match_script_formulas():
    eq = [("2020-01-01", 100.0), ("2026-08-30", 110.0)]
    s = summarize(eq, N0=100.0, bench={}, start="2020-01-01", end="2026-08-30")
    yrs = (__import__("datetime").date(2026, 8, 30) - __import__("datetime").date(2020, 1, 1)).days / 365.25
    assert round(s["cagr"], 4) == round(((110.0 / 100.0) ** (1 / yrs) - 1) * 100, 4)
    assert "total_return" in s
    assert "sharpe_ratio" in s
    assert "max_drawdown" in s
    assert "benchmark_cagr" in s
    assert "alpha" in s


def test_brokerage_reduces_final_equity(monkeypatch):
    """include_brokerage=True with a non-zero rate must reduce final equity vs gross."""
    ever = ["AAA"]
    import wave_engine
    monkeypatch.setattr(wave_engine, "members_as_of", lambda intervals, d: set(ever))
    dates = ["2020-01-01", "2020-01-02"]
    indicators = {
        "AAA": {
            "2020-01-01": {"mean_long": 45},
            "2020-01-02": {"mean_long": 60},
        }
    }
    prices = {"AAA": {"2020-01-01": 100.0, "2020-01-02": 110.0}}
    wave = WaveConfig(
        capital_N=100000, tranches=20, reentry=True, buy_cadence="daily", exit_mode="full",
        entry=[[{"kind": "compare", "lhs_metric": "mean_long", "op": ">", "rhs": {"number": 50}}]],
        accumulate=[],
        exit=[[{"kind": "compare", "lhs_metric": "mean_long", "op": "<", "rhs": {"number": 0}}]],
    )
    gross = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave,
                                include_brokerage=False, point_in_time=False)
    with_costs = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave,
                                     include_brokerage=True, custom_brokerage_rate=0.002,
                                     point_in_time=False)
    assert with_costs["equity_curve"][-1][1] < gross["equity_curve"][-1][1]


def test_gross_equity_curve_unchanged_by_task7(monkeypatch):
    """Default (include_brokerage=False, point_in_time=False) must produce same equity as Task 6."""
    ever = ["AAA"]
    import wave_engine
    monkeypatch.setattr(wave_engine, "members_as_of", lambda intervals, d: set(ever))
    dates = ["2020-01-01", "2020-01-02", "2020-01-03"]
    indicators = {"AAA": {"2020-01-01": {"mean_long": 38}, "2020-01-02": {"mean_long": 62},
                          "2020-01-03": {"mean_long": 72}}}
    prices = {"AAA": {"2020-01-01": 100.0, "2020-01-02": 100.0, "2020-01-03": 110.0}}
    g = lambda L: [{"kind": "cross", "metric": "mean_long", "level": L, "direction": "up"},
                   {"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up", "negate": True}]
    wave = WaveConfig(capital_N=300000, tranches=20, reentry=True, buy_cadence="daily", exit_mode="full",
                      entry=[g(40), g(50), g(60)], accumulate=[],
                      exit=[[{"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up"}],
                            [{"kind": "cross", "metric": "mean_long", "level": 40, "direction": "down"}]])
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave,
                              include_brokerage=False, point_in_time=False)
    assert round(out["equity_curve"][-1][1], 2) == 304500.00


def test_results_list_structure(monkeypatch):
    """Returned dict must include 'results' list with expected per-day keys."""
    ever = ["AAA"]
    import wave_engine
    monkeypatch.setattr(wave_engine, "members_as_of", lambda intervals, d: set(ever))
    dates = ["2020-01-01", "2020-01-02"]
    indicators = {"AAA": {"2020-01-01": {"mean_long": 60}, "2020-01-02": {"mean_long": 60}}}
    prices = {"AAA": {"2020-01-01": 100.0, "2020-01-02": 105.0}}
    wave = WaveConfig(
        capital_N=100000, tranches=10, reentry=True, buy_cadence="daily", exit_mode="full",
        entry=[[{"kind": "compare", "lhs_metric": "mean_long", "op": ">", "rhs": {"number": 50}}]],
        accumulate=[],
        exit=[[{"kind": "compare", "lhs_metric": "mean_long", "op": "<", "rhs": {"number": 0}}]],
    )
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave, point_in_time=False)
    assert "results" in out
    assert "summary" in out
    assert "wave_diagnostics" in out
    row = out["results"][0]
    assert "date" in row
    assert "portfolio_value" in row
    assert "benchmark_value" in row
    assert "holdings" in row
    assert "new_added" in row
    assert "exited" in row
