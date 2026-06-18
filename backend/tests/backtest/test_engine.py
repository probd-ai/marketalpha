from datetime import date
import numpy as np
import pandas as pd
from app.strategies.base import BacktestConfig
from app.strategies.rs_momentum import RSMomentumStrategy
from app.backtest.engine import BacktestEngine, monthly_rebalance_dates


def test_monthly_rebalance_dates_first_per_month():
    idx = pd.bdate_range("2024-01-01", "2024-03-15")
    dates = monthly_rebalance_dates(idx)
    months = sorted({(d.year, d.month) for d in dates})
    assert months == [(2024, 1), (2024, 2), (2024, 3)]
    assert dates[0] == idx[0].date()


class _StubSource:
    def __init__(self, panel, nifty):
        self._panel, self._nifty = panel, nifty

    def get_ohlc(self, symbol, exchange, interval, start, end):
        s = self._nifty if symbol == "NIFTY" else self._panel[symbol]
        return pd.DataFrame({"close": s})


class _StubProvider:
    def __init__(self, symbols):
        self._symbols = set(symbols)

    def members_as_of(self, index, on):
        return self._symbols

    def union_over(self, index, start, end):
        return self._symbols


class _RecordingStrategy:
    """Captures every signal_date passed via select(); always returns a uniform weight."""

    def __init__(self, symbols):
        self._symbols = list(symbols)
        self.called_as_of: list[date] = []

    def select(self, panel, nifty, members, as_of, cfg):
        self.called_as_of.append(as_of)
        w = 1.0 / len(self._symbols) if self._symbols else 1.0
        return {s: w for s in self._symbols}


def test_signal_uses_prior_close_not_rebalance_date():
    # Build a panel spanning >2 months so we get ≥1 rebalance with a prior bar.
    idx = pd.bdate_range("2024-01-01", periods=70)
    symbols = ["A", "B"]
    panel = pd.DataFrame({s: 100.0 + np.arange(len(idx)) for s in symbols}, index=idx)
    nifty = pd.Series(100.0, index=idx)
    cfg = BacktestConfig(start=idx[0].date(), end=idx[-1].date(),
                         lookback=10, top_pct=1.0, top_n=2, nominal_size=2)
    strategy = _RecordingStrategy(symbols)
    engine = BacktestEngine(_StubSource(panel, nifty), _StubProvider(symbols), strategy)
    result = engine.run(cfg)

    rebal_dates = [d for d, _ in result.holdings]
    assert len(strategy.called_as_of) >= 1, "strategy must be called at least once"
    for signal_date, fill_date in zip(strategy.called_as_of, rebal_dates):
        assert signal_date < fill_date, (
            f"signal_date {signal_date} must be strictly before fill_date {fill_date}"
        )


def test_engine_runs_end_to_end():
    idx = pd.bdate_range("2024-01-01", periods=70)
    panel = pd.DataFrame({f"S{i}": 100.0 + i * np.arange(len(idx)) for i in range(6)}, index=idx)
    nifty = pd.Series(100.0, index=idx)
    cfg = BacktestConfig(start=idx[0].date(), end=idx[-1].date(),
                         lookback=22, top_pct=0.5, top_n=2, nominal_size=6)
    engine = BacktestEngine(_StubSource(panel, nifty), _StubProvider(panel.columns),
                            RSMomentumStrategy())
    result = engine.run(cfg)
    assert len(result.equity) > 0
    assert "sharpe" in result.metrics
    assert result.equity.iloc[0] > 0
