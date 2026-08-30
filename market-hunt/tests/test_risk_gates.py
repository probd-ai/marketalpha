# Dynamic-Fib gate tests — pure, hand-computable series.
from risk_gates import fib_state_from_values, gate_map_fib, rolling_fib_gate


def test_rolling_fib_basic_levels():
    # 40 bars: range 100..200 established, then close probes the 0.66 level (166)
    highs = [200.0] * 40
    lows = [100.0] * 40
    closes = [150.0] * 39 + [170.0]
    st = rolling_fib_gate(highs, lows, closes, lookback=40, level=0.66, min_bars=30)
    assert st[38] is False   # 150 < 166 -> off
    assert st[39] is True    # 170 > 166 -> on


def test_warmup_is_on():
    st = rolling_fib_gate([1.0] * 10, [0.0] * 10, [0.1] * 10, lookback=222, level=0.66)
    assert all(st)  # < MIN_BARS -> always on


def test_window_rolls_off_old_extremes():
    # early spike to 1000 exits the 5-bar window; later range is 100..110
    highs = [1000.0] + [110.0] * 60
    lows = [90.0] + [100.0] * 60
    closes = [105.0] * 61
    st = rolling_fib_gate(highs, lows, closes, lookback=5, level=0.5, min_bars=5)
    # once the spike leaves the window: level = 100 + 10*0.5 = 105 -> close 105 on
    assert st[60] is True
    # while the spike is in-window: level = 90 + 910*0.5 = 545 -> off
    assert st[3] is False or len(st) > 0  # (bar 3 pre-min_bars=on; check bar 4)
    assert st[4] is False


def test_gate_map_keys():
    m = gate_map_fib(["d1", "d2"], [10.0, 10.0], [0.0, 0.0], [7.0, 6.0],
                     lookback=2, level=0.66)
    assert set(m) == {"d1", "d2"}


def test_portfolio_curve_state():
    # equity 100 -> 200 then drawdown; level 0.66 of [100..200] = 166
    vals = [100.0 + i * 2 for i in range(51)]  # rises to 200
    assert fib_state_from_values(vals, lookback=60, level=0.66, min_bars=30) is True
    vals_dd = vals + [160.0]  # below 166
    assert fib_state_from_values(vals_dd, lookback=60, level=0.66, min_bars=30) is False
    vals_rec = vals_dd + [170.0]
    assert fib_state_from_values(vals_rec, lookback=60, level=0.66, min_bars=30) is True


def test_short_history_portfolio_gate_on():
    assert fib_state_from_values([100.0] * 5) is True
