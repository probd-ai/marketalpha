"""Trimmed walk-forward run: tune portfolio size only, fixed 3m window, select by
CAGR (avoids the sim's inf-Sharpe), 4 folds. Prints per-fold progress live so the
pattern is visible incrementally and the run is interruptible without losing it.
"""
import asyncio
import math
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from walkforward.folds import make_folds                      # noqa: E402
from walkforward.sim_runner import SimRunner                  # noqa: E402
from walkforward.walk_forward import select_best, FoldResult  # noqa: E402
from walkforward import report                                # noqa: E402

SIZES = (10, 20, 30)
WINDOW = "price_roc_66d"
BASELINE_N = 20
PLOT = os.path.join(os.environ.get("TEMP", "."), "wf_oos_trim.png")


async def main():
    # data 2019..2025 -> 4 rolling folds: test 2022, 2023, 2024, 2025
    folds = make_folds(date(2019, 1, 1), date(2025, 12, 31))
    print(f"folds: {[(f.train_start.isoformat(), f.test_start.year) for f in folds]}", flush=True)
    r = SimRunner()
    await r.setup()
    results = []
    try:
        for i, f in enumerate(folds, 1):
            grid = {}
            for n in SIZES:
                rr = await r.run(f.train_start, f.train_end, n, WINDOW)
                grid[(n, WINDOW)] = rr
                msg = (f"cagr={rr.cagr*100:.1f}% sharpe={rr.sharpe:.2f}" if rr else "None")
                print(f"  fold{i} train[{f.train_start.year}-{f.train_end.year}] N={n}: {msg}", flush=True)
            best = select_best({k: v for k, v in grid.items() if v}, "cagr")
            oos = await r.run(f.test_start, f.test_end, best[0], best[1])
            base = oos if best[0] == BASELINE_N else await r.run(f.test_start, f.test_end, BASELINE_N, WINDOW)
            results.append(FoldResult(f, best, grid[best], oos, base))
            print(f"FOLD{i} test{f.test_start.year}: best N={best[0]} | "
                  f"OOS cagr={oos.cagr*100:.1f}% sharpe={oos.sharpe:.2f} | "
                  f"baselineN20 OOS cagr={base.cagr*100:.1f}%", flush=True)

        print("\n" + report.format_fold_table(results), flush=True)
        adapt, base_m, bench, ac, bc, nc = report.build_curves(results, r)
        for nm, m in [("adaptive", adapt), ("fixedN20", base_m), ("Nifty50", bench)]:
            print(f"{nm:10} stitched-OOS: cagr={m['cagr']*100:6.1f}%  sharpe={m['sharpe']:.2f}  "
                  f"maxDD={m.get('max_drawdown', float('nan')):.1f}", flush=True)
        if math.isnan(bench.get("cagr", float("nan"))):
            print("WARNING: benchmark (Nifty 50) curve empty — verdict benchmark comparison unreliable.", flush=True)
        print(f"stability: {report.stability_flag(results)}", flush=True)
        print(f"VERDICT:   {report.verdict(adapt, base_m, bench)}", flush=True)
        try:
            report.save_plot(ac, bc, nc, PLOT)
            print(f"plot -> {PLOT}", flush=True)
        except Exception as e:
            print(f"plot skipped: {e}", flush=True)
    finally:
        await r.aclose()


if __name__ == "__main__":
    asyncio.run(main())
