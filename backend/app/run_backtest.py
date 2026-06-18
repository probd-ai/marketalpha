import argparse
from datetime import date, timedelta
from app.core.config import settings
from app.data.sources.tvdatafeed_source import TvDatafeedSource
from app.data.cache.parquet_cache import ParquetCache, CachedDataSource
from app.data.universe.provider import load_provider_from_seed
from app.strategies.base import BacktestConfig
from app.strategies.rs_momentum import RSMomentumStrategy
from app.backtest.engine import BacktestEngine, BacktestResult


def main(argv: list[str] | None = None) -> BacktestResult:
    parser = argparse.ArgumentParser(description="Run RS-Momentum backtest")
    parser.add_argument("--index", default="NIFTY50")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--cost-bps", type=float, default=0.0)
    args = parser.parse_args(argv)

    end = date.today()
    start = end - timedelta(days=365 * args.years)
    cfg = BacktestConfig(index=args.index, start=start, end=end,
                         starting_capital=args.capital, cost_bps=args.cost_bps)

    source = CachedDataSource(
        TvDatafeedSource(settings.tv_username, settings.tv_password),
        ParquetCache(settings.cache_dir),
    )
    provider = load_provider_from_seed(settings.seed_dir, anchor=end)
    engine = BacktestEngine(source, provider, RSMomentumStrategy())
    result = engine.run(cfg)

    print("=== RS-Momentum backtest ===")
    for k, v in result.metrics.items():
        print(f"{k:>24}: {v:,.4f}" if isinstance(v, float) else f"{k:>24}: {v}")
    print(f"{'warnings':>24}: {len(result.warnings)}")
    return result


if __name__ == "__main__":
    main()
