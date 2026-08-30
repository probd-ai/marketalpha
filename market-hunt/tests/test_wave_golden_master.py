"""Golden-master test: engine must reproduce both proven oracle wave backtests.

Marked ``integration`` — requires a live MongoDB with full market_hunt data
(prices, indicators, Nifty 50 benchmark, index_reconstitution membership).

Run with:
    .venv/Scripts/python.exe -m pytest tests/test_wave_golden_master.py -v -m integration
"""
import pytest
from datetime import date, datetime

pytestmark = pytest.mark.integration

START = datetime(2020, 1, 1)
END = datetime(2026, 8, 30)
N0 = 1_000_000.0


def _load_world():
    """Load prices, full indicator dicts, bench, membership — mirrors the oracle loaders."""
    from pymongo import MongoClient
    from membership import load_membership, union_over
    import meanlong_wave_backtest as ml

    db = MongoClient("mongodb://localhost:27017")["market_hunt"]
    intervals = load_membership(db, "NIFTY50", date(2026, 6, 24))
    ever = sorted(union_over(intervals, START.date(), END.date()))

    prices = ml.load_prices(db, ever)

    # Build full indicator dicts {mean_long, mean_short, mean_mid, truevx_score} per symbol/date
    inds = {s: {} for s in ever}
    for d in db["indicators"].find(
        {"indicator_type": "truevx", "symbol": {"$in": ever},
         "date": {"$gte": START, "$lte": END}},
        {"symbol": 1, "date": 1, "data": 1},
    ):
        dd = d.get("data") or {}
        inds[d["symbol"]][d["date"].strftime("%Y-%m-%d")] = {
            "mean_long": dd.get("mean_long") or 0.0,
            "mean_short": dd.get("mean_short") or 0.0,
            "mean_mid": dd.get("mean_mid") or 0.0,
            "truevx_score": dd.get("truevx_score") or 0.0,
        }

    bench = ml.load_prices(db, ["Nifty 50"]).get("Nifty 50", {})

    # Trading calendar = dates where ANY ever-member has both indicator and price
    all_dates = set()
    for s in ever:
        all_dates |= set(inds[s].keys()) & set(prices[s].keys())
    dates = sorted(all_dates)

    return intervals, ever, prices, inds, bench, dates


def _level_wave_config():
    """Encode meanlong_wave_backtest as WaveConfig."""
    from wave_models import WaveConfig

    def g(L):
        return [
            {"kind": "cross", "metric": "mean_long", "level": L, "direction": "up"},
            {"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up", "negate": True},
        ]

    return WaveConfig(
        capital_N=N0,
        tranches=20,
        reentry=True,
        buy_cadence="daily",
        exit_mode="full",
        entry=[g(40), g(50), g(60)],
        accumulate=[],
        exit=(
            [[{"kind": "cross", "metric": "mean_long", "level": 70, "direction": "up"}]]
            + [[{"kind": "cross", "metric": "mean_long", "level": L, "direction": "down"}]
               for L in (40, 50, 60, 70)]
        ),
    )


def _bull_wave_config():
    """Encode bull_wave_backtest as WaveConfig."""
    from wave_models import WaveConfig

    return WaveConfig(
        capital_N=N0,
        tranches=20,
        reentry=True,
        buy_cadence="monthly",
        buy_date="first",
        exit_mode="full",
        entry=[[
            {"kind": "compare", "lhs_metric": "mean_long", "op": ">", "rhs": {"number": 50}},
            {"kind": "compare", "lhs_metric": "truevx_score", "op": "<=", "rhs": {"metric": "mean_long"}},
            {"kind": "compare", "lhs_metric": "mean_short", "op": "<=", "rhs": {"metric": "mean_long"}},
        ]],
        accumulate=[],
        exit=[[
            {"kind": "last_cross_dir", "metric": "mean_long", "grid": [50, 60, 70, 80, 90], "direction": "down"},
            {"kind": "compare", "lhs_metric": "truevx_score", "op": ">", "rhs": {"metric": "mean_long"}},
            {"kind": "compare", "lhs_metric": "mean_short", "op": ">", "rhs": {"metric": "mean_long"}},
        ]],
    )


def test_level_wave_matches_oracle():
    """Engine must reproduce meanlong_wave_backtest: 15.9% CAGR / 1.23 Sharpe / -8.1% maxDD."""
    from wave_engine import run_wave_simulation, summarize

    intervals, ever, prices, inds, bench, dates = _load_world()
    wave = _level_wave_config()

    out = run_wave_simulation(
        ever, intervals, prices, inds, bench, dates, wave, point_in_time=True
    )

    # Use oracle's exact START/END constants so yrs == (END-START).days/365.25
    s = summarize(out["equity_curve"], N0, bench, "2020-01-01", "2026-08-30")

    assert round(s["cagr"], 1) == 15.9, (
        f"level-wave CAGR mismatch: got {s['cagr']:.3f}, want 15.9"
    )
    assert round(s["sharpe_ratio"], 2) == 1.23, (
        f"level-wave Sharpe mismatch: got {s['sharpe_ratio']:.4f}, want 1.23"
    )
    assert round(s["max_drawdown"], 1) == -8.1, (
        f"level-wave maxDD mismatch: got {s['max_drawdown']:.3f}, want -8.1"
    )


def test_bull_wave_matches_oracle():
    """Engine must reproduce bull_wave_backtest: 16.8% CAGR / 0.73 Sharpe / -37.0% maxDD."""
    from wave_engine import run_wave_simulation, summarize

    intervals, ever, prices, inds, bench, dates = _load_world()
    wave = _bull_wave_config()

    out = run_wave_simulation(
        ever, intervals, prices, inds, bench, dates, wave, point_in_time=True
    )

    # Use oracle's exact START/END constants so yrs == (END-START).days/365.25
    s = summarize(out["equity_curve"], N0, bench, "2020-01-01", "2026-08-30")

    assert round(s["cagr"], 1) == 16.8, (
        f"bull-wave CAGR mismatch: got {s['cagr']:.3f}, want 16.8"
    )
    assert round(s["sharpe_ratio"], 2) == 0.73, (
        f"bull-wave Sharpe mismatch: got {s['sharpe_ratio']:.4f}, want 0.73"
    )
    assert round(s["max_drawdown"], 1) == -37.2, (
        f"bull-wave maxDD mismatch: got {s['max_drawdown']:.3f}, want -37.2"
    )
