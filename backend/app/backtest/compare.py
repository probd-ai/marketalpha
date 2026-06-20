from dataclasses import dataclass
from app.backtest.engine import BacktestEngine, BacktestResult
from app.backtest.coverage import coverage_report, CoverageReport
from app.strategies.base import BacktestConfig, Strategy
from app.data.sources.base import DataSource
from app.data.universe.provider import UniverseProvider


@dataclass
class ComparisonResult:
    biased: BacktestResult
    point_in_time: BacktestResult
    delta: dict
    coverage: CoverageReport


def run_comparison(source: DataSource, pit_provider: UniverseProvider,
                   biased_provider: UniverseProvider, strategy: Strategy,
                   cfg: BacktestConfig, index_symbol: str = "NIFTY",
                   exchange: str = "NSE") -> ComparisonResult:
    pit = BacktestEngine(source, pit_provider, strategy, index_symbol, exchange).run(cfg)
    biased = BacktestEngine(source, biased_provider, strategy, index_symbol, exchange).run(cfg)

    symbols = pit_provider.union_over(cfg.index, cfg.start, cfg.end)
    cov = coverage_report(source, symbols, exchange, cfg.start, cfg.end)

    b_final = float(biased.equity.iloc[-1]) if len(biased.equity) else 0.0
    p_final = float(pit.equity.iloc[-1]) if len(pit.equity) else 0.0
    delta = {
        "biased_final": b_final,
        "pit_final": p_final,
        "final_abs": b_final - p_final,
        "final_pct": (b_final - p_final) / p_final if p_final else float("nan"),
    }
    return ComparisonResult(biased=biased, point_in_time=pit, delta=delta, coverage=cov)
