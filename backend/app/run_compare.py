import argparse
from datetime import date, timedelta
from app.core.config import settings
from app.data.sources.mongo_source import MongoDataSource, INDEX_SYMBOL_MAP
from app.data.universe.provider import (
    load_provider_from_seed, load_biased_provider_from_seed,
)
from app.strategies.base import BacktestConfig
from app.strategies.rs_momentum import RSMomentumStrategy
from app.backtest.compare import run_comparison, ComparisonResult


def main(argv: list[str] | None = None) -> ComparisonResult:
    parser = argparse.ArgumentParser(description="Biased vs point-in-time RS-Momentum")
    parser.add_argument("--index", default="NIFTY50")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--capital", type=float, default=1_000_000)
    parser.add_argument("--cost-bps", type=float, default=0.0)
    args = parser.parse_args(argv)

    end = date.today()
    start = end - timedelta(days=365 * args.years)
    cfg = BacktestConfig(index=args.index, start=start, end=end,
                         starting_capital=args.capital, cost_bps=args.cost_bps)

    source = MongoDataSource(settings.mongo_uri, settings.mongo_db, INDEX_SYMBOL_MAP)
    pit = load_provider_from_seed(settings.seed_dir, anchor=end)
    biased = load_biased_provider_from_seed(settings.seed_dir)
    res = run_comparison(source, pit, biased, RSMomentumStrategy(), cfg)

    def show(name, r):
        print(f"\n=== {name} ===")
        for k, v in r.metrics.items():
            print(f"{k:>24}: {v:,.4f}" if isinstance(v, float) else f"{k:>24}: {v}")
        print(f"{'warnings':>24}: {len(r.warnings)}")

    show("BIASED (today's list)", res.biased)
    show("POINT-IN-TIME", res.point_in_time)
    print("\n=== DELTA ===")
    for k, v in res.delta.items():
        print(f"{k:>24}: {v:,.4f}")
    print(f"\nCoverage: {len(res.coverage.covered)}/{res.coverage.total} covered, "
          f"{len(res.coverage.missing)} missing")
    if res.coverage.missing:
        print("Missing (no prices in Mongo): " + ", ".join(res.coverage.missing))
    return res


if __name__ == "__main__":
    main()
