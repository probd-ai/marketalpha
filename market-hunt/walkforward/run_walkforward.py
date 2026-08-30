import argparse
import asyncio
from datetime import datetime
from walkforward.folds import make_folds
from walkforward.sim_runner import SimRunner, GRID_SIZES, GRID_WINDOWS
from walkforward.walk_forward import run_walk_forward
from walkforward import report


async def main(argv=None):
    p = argparse.ArgumentParser(description="Walk-forward validation of RS-Momentum")
    p.add_argument("--data-start", default="2018-01-01")
    p.add_argument("--data-end", default="2026-06-30")
    p.add_argument("--objective", default="sharpe", choices=["sharpe", "cagr"])
    p.add_argument("--baseline-n", type=int, default=20)
    p.add_argument("--baseline-window", default="price_roc_66d")
    p.add_argument("--plot", default=None)
    args = p.parse_args(argv)

    ds = datetime.strptime(args.data_start, "%Y-%m-%d").date()
    de = datetime.strptime(args.data_end, "%Y-%m-%d").date()
    folds = make_folds(ds, de)
    print(f"folds: {len(folds)}  grid: {len(GRID_SIZES)}x{len(GRID_WINDOWS)} "
          f"= {len(GRID_SIZES) * len(GRID_WINDOWS)} configs/fold")

    runner = SimRunner()
    await runner.setup()
    try:
        results = await run_walk_forward(
            runner, folds, GRID_SIZES, GRID_WINDOWS,
            baseline=(args.baseline_n, args.baseline_window), objective=args.objective)
        print("\n" + report.format_fold_table(results))
        adapt, base, bench, ac, bc, nc = report.build_curves(results, runner)
        import math
        if math.isnan(bench.get("cagr", float("nan"))):
            print("WARNING: benchmark (Nifty 50) curve is empty — verdict's benchmark comparison is unreliable.")
        print(f"\nstitched OOS  adaptive: CAGR {adapt['cagr']*100:5.1f}%  Sharpe {adapt['sharpe']:.2f}")
        print(f"stitched OOS  fixed   : CAGR {base['cagr']*100:5.1f}%  Sharpe {base['sharpe']:.2f}")
        print(f"stitched OOS  Nifty50 : CAGR {bench['cagr']*100:5.1f}%  Sharpe {bench['sharpe']:.2f}")
        print(f"\nstability: {report.stability_flag(results)}")
        print(f"VERDICT:   {report.verdict(adapt, base, bench)}")
        if args.plot:
            report.save_plot(ac, bc, nc, args.plot)
            print(f"plot -> {args.plot}")
    finally:
        await runner.aclose()


if __name__ == "__main__":
    asyncio.run(main())
