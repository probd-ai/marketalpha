from dataclasses import dataclass, field
from datetime import date
import pandas as pd

from app.backtest.portfolio import simulate
from app.backtest.metrics import compute_metrics
from app.strategies.base import BacktestConfig, Strategy
from app.data.sources.base import DataSource
from app.data.universe.provider import UniverseProvider


def monthly_rebalance_dates(trading_index: pd.DatetimeIndex) -> list[date]:
    seen: dict[tuple[int, int], date] = {}
    for ts in sorted(trading_index):
        key = (ts.year, ts.month)
        if key not in seen:
            seen[key] = ts.date()
    return sorted(seen.values())


@dataclass
class BacktestResult:
    equity: pd.Series
    benchmark: pd.Series
    metrics: dict
    holdings: list[tuple[date, dict[str, float]]]
    warnings: list[str] = field(default_factory=list)


class BacktestEngine:
    def __init__(
        self,
        source: DataSource,
        provider: UniverseProvider,
        strategy: Strategy,
        index_symbol: str = "NIFTY",
        exchange: str = "NSE",
    ) -> None:
        self._source = source
        self._provider = provider
        self._strategy = strategy
        self._index_symbol = index_symbol
        self._exchange = exchange

    def _close(self, symbol: str, cfg: BacktestConfig) -> pd.Series:
        df = self._source.get_ohlc(symbol, self._exchange, "daily", cfg.start, cfg.end)
        return df["close"] if "close" in df.columns else pd.Series(dtype=float)

    def run(self, cfg: BacktestConfig) -> BacktestResult:
        warnings: list[str] = []

        nifty = self._close(self._index_symbol, cfg)
        symbols = sorted(self._provider.union_over(cfg.index, cfg.start, cfg.end))

        series: dict[str, pd.Series] = {}
        for s in symbols:
            close = self._close(s, cfg)
            if close.empty:
                warnings.append(f"no data for {s}")
                continue
            series[s] = close

        panel = pd.DataFrame(series)

        rebal_dates = monthly_rebalance_dates(panel.index)
        panel_dates = sorted(panel.index)
        schedule: list[tuple[date, dict[str, float]]] = []
        for d in rebal_dates:
            prior = [ts for ts in panel_dates if ts.date() < d]
            if not prior:
                warnings.append(f"no prior close for rebalance {d}")
                continue
            signal_date = prior[-1].date()
            members = self._provider.members_as_of(cfg.index, d)
            weights = self._strategy.select(panel, nifty, members, signal_date, cfg)
            if weights:
                schedule.append((d, weights))
            else:
                warnings.append(f"no selection on {d}")

        sim = simulate(panel, schedule, cfg.starting_capital, cfg.cost_bps)

        if schedule:
            bench = nifty[nifty.index >= pd.Timestamp(schedule[0][0])]
        else:
            bench = nifty
        if len(bench):
            bench = bench / bench.iloc[0] * cfg.starting_capital

        metrics = compute_metrics(sim.equity, bench, sim.turnover)
        return BacktestResult(sim.equity, bench, metrics, sim.holdings, warnings)
