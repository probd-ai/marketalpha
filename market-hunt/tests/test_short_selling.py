"""Short-selling support — pure unit tests (no Mongo).

Covers:
  * wave engine sign-flip: sell-to-open, profit on decline, loss on rise, borrow cost
  * the long path (side='long', the default) is an exact no-op (sign=1)
  * the stateless `_short_rebalance` helper: full-turnover short, equity preserved,
    charges reduce net equity

Short accounting model (whole-run, one side):
    equity = cash + sign * Σ(shares · price)   with sign = -1 for short
    open short  → cash += notional (sell borrowed shares)
    cover short → cash -= shares · price (buy them back)
    daily borrow cost accrues on short market value.
"""
from wave_models import WaveConfig
from wave_engine import run_wave_simulation


def _members_patch(monkeypatch, ever):
    import wave_engine
    monkeypatch.setattr(wave_engine, "members_as_of", lambda intervals, d: set(ever))


def _held_wave():
    """Enter when mean_long>50 (compare fires daily, capped to 1 tranche); never exits."""
    return WaveConfig(
        capital_N=100000, tranches=1, reentry=True, buy_cadence="daily", exit_mode="full",
        entry=[[{"kind": "compare", "lhs_metric": "mean_long", "op": ">", "rhs": {"number": 50}}]],
        accumulate=[],
        exit=[[{"kind": "compare", "lhs_metric": "mean_long", "op": "<", "rhs": {"number": 0}}]],
    )


# --- wave engine: short accounting -----------------------------------------

def test_wave_short_profits_when_price_falls(monkeypatch):
    ever = ["AAA"]; _members_patch(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02"]
    indicators = {"AAA": {d: {"mean_long": 60} for d in dates}}
    prices = {"AAA": {"2020-01-01": 100.0, "2020-01-02": 90.0}}  # -10%
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, _held_wave(),
                              point_in_time=False, side="short")
    # open 1000 sh @100 → proceeds 100000, cash 200000, MV 100000 → equity 100000
    assert round(out["equity_curve"][0][1], 2) == 100000.00
    # day2 price 90 → MV 90000 → equity 200000 - 90000 = 110000 (+10%)
    assert round(out["equity_curve"][-1][1], 2) == 110000.00


def test_wave_short_loses_when_price_rises(monkeypatch):
    ever = ["AAA"]; _members_patch(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02"]
    indicators = {"AAA": {d: {"mean_long": 60} for d in dates}}
    prices = {"AAA": {"2020-01-01": 100.0, "2020-01-02": 110.0}}  # +10%
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, _held_wave(),
                              point_in_time=False, side="short")
    assert round(out["equity_curve"][-1][1], 2) == 90000.00  # short loses 10%


def test_wave_long_loses_when_short_profits(monkeypatch):
    """Same falling-price scenario: the long twin loses what the short gains."""
    ever = ["AAA"]; _members_patch(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02"]
    indicators = {"AAA": {d: {"mean_long": 60} for d in dates}}
    prices = {"AAA": {"2020-01-01": 100.0, "2020-01-02": 90.0}}
    lng = run_wave_simulation(ever, None, prices, indicators, {}, dates, _held_wave(),
                              point_in_time=False, side="long")
    assert round(lng["equity_curve"][-1][1], 2) == 90000.00


def test_wave_borrow_cost_reduces_short_equity(monkeypatch):
    ever = ["AAA"]; _members_patch(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02"]
    indicators = {"AAA": {d: {"mean_long": 60} for d in dates}}
    prices = {"AAA": {"2020-01-01": 100.0, "2020-01-02": 90.0}}
    free = run_wave_simulation(ever, None, prices, indicators, {}, dates, _held_wave(),
                               point_in_time=False, side="short", annual_borrow_rate=0.0)
    paid = run_wave_simulation(ever, None, prices, indicators, {}, dates, _held_wave(),
                               point_in_time=False, side="short", annual_borrow_rate=0.10)
    assert paid["equity_curve"][-1][1] < free["equity_curve"][-1][1]


def test_wave_short_holdings_pnl_positive_on_decline(monkeypatch):
    ever = ["AAA"]; _members_patch(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02"]
    indicators = {"AAA": {d: {"mean_long": 60} for d in dates}}
    prices = {"AAA": {"2020-01-01": 100.0, "2020-01-02": 90.0}}
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, _held_wave(),
                              point_in_time=False, side="short")
    h = out["results"][-1]["holdings"][0]
    assert h["side"] == "short"
    assert h["pnl"] > 0           # price fell, short is in profit
    assert h["pnl_percent"] > 0


def test_wave_default_side_long_unchanged(monkeypatch):
    """Default side keeps the documented level-wave equity (304500) — sign=1 no-op."""
    ever = ["AAA"]; _members_patch(monkeypatch, ever)
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
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, wave, point_in_time=False)
    assert round(out["equity_curve"][-1][1], 2) == 304500.00


def test_wave_short_ruin_floors_equity_at_zero(monkeypatch):
    """A short that more than doubles against you wipes the account: equity is
    floored at 0 (margin call), never negative, and `ruined` is flagged."""
    ever = ["AAA"]; _members_patch(monkeypatch, ever)
    dates = ["2020-01-01", "2020-01-02", "2020-01-03"]
    indicators = {"AAA": {d: {"mean_long": 60} for d in dates}}
    # open @100; price rockets to 250 → short liability 2.5x proceeds → ruin
    prices = {"AAA": {"2020-01-01": 100.0, "2020-01-02": 250.0, "2020-01-03": 300.0}}
    out = run_wave_simulation(ever, None, prices, indicators, {}, dates, _held_wave(),
                              point_in_time=False, side="short")
    eq = [e for _, e in out["equity_curve"]]
    assert min(eq) >= 0.0                       # never negative
    assert eq[-1] == 0.0                         # wiped out, carried at 0
    assert out["diagnostics"]["ruined"] is True
    assert out["summary"]["ruined"] is True


# --- stateless short rebalance helper --------------------------------------

def _params(include_brokerage=False, rebalance_type="equal_weight", exchange="NSE",
            custom_brokerage_rate=0.0, side="short", annual_borrow_rate=0.0):
    from types import SimpleNamespace
    return SimpleNamespace(include_brokerage=include_brokerage, rebalance_type=rebalance_type,
                           exchange=exchange, custom_brokerage_rate=custom_brokerage_rate,
                           side=side, annual_borrow_rate=annual_borrow_rate)


def test_short_rebalance_preserves_equity_no_charges():
    from engine.stateless import _short_rebalance
    day_prices = {"AAA": {"close_price": 100.0}, "BBB": {"close_price": 200.0}}
    out = _short_rebalance({}, ["AAA", "BBB"], day_prices, 100000.0, _params(), {})
    h = out["new_holdings"]
    assert round(h["AAA"]["shares"], 6) == 500.0    # 50000 / 100
    assert round(h["BBB"]["shares"], 6) == 250.0    # 50000 / 200
    mv = sum(h[s]["shares"] * day_prices[s]["close_price"] for s in h)
    assert round(out["remaining_cash"] - mv, 2) == 100000.00   # net short equity preserved


def test_short_rebalance_covers_existing_then_reopens():
    from engine.stateless import _short_rebalance
    # currently short CCC; rebalance into AAA only — CCC must be covered out.
    day_prices = {"AAA": {"close_price": 100.0}, "CCC": {"close_price": 50.0}}
    current = {"CCC": {"shares": 200.0, "avg_price": 60.0}}  # equity passed in by caller
    out = _short_rebalance(current, ["AAA"], day_prices, 100000.0, _params(), {})
    assert "CCC" not in out["new_holdings"]
    assert "AAA" in out["new_holdings"]
    mv = sum(out["new_holdings"][s]["shares"] * day_prices[s]["close_price"] for s in out["new_holdings"])
    assert round(out["remaining_cash"] - mv, 2) == 100000.00


def test_short_rebalance_charges_reduce_net_equity():
    from engine.stateless import _short_rebalance
    day_prices = {"AAA": {"close_price": 100.0}, "BBB": {"close_price": 200.0}}
    free = _short_rebalance({}, ["AAA", "BBB"], day_prices, 100000.0,
                            _params(include_brokerage=False), {})
    paid = _short_rebalance({}, ["AAA", "BBB"], day_prices, 100000.0,
                            _params(include_brokerage=True), {})
    fm = sum(free["new_holdings"][s]["shares"] * day_prices[s]["close_price"] for s in free["new_holdings"])
    pm = sum(paid["new_holdings"][s]["shares"] * day_prices[s]["close_price"] for s in paid["new_holdings"])
    assert (paid["remaining_cash"] - pm) < (free["remaining_cash"] - fm)
    assert paid["daily_charges"]["total_charges"] > 0
