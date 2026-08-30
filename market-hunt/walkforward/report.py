from walkforward.walk_forward import curve_metrics, stitch


def verdict(adaptive: dict, baseline: dict, benchmark: dict) -> str:
    if baseline["cagr"] <= benchmark["cagr"]:
        return "NO_EDGE"
    if adaptive["sharpe"] >= baseline["sharpe"]:
        return "ROBUST_OPTIMIZED"
    return "ROBUST_FIXED"


def stability_flag(fold_results) -> str:
    distinct = {fr.best_config for fr in fold_results}
    return "STABLE" if len(distinct) <= 2 else "THRASHING"


def format_fold_table(fold_results) -> str:
    lines = [f"{'fold':>4} {'best (N, window)':>26} {'trainSharpe':>11} "
             f"{'oosSharpe':>9} {'oosCAGR%':>9} {'oosAlpha':>9}"]
    for i, fr in enumerate(fold_results, 1):
        cfg = f"{fr.best_config[0]}, {fr.best_config[1]}"
        oos_cagr = fr.oos.cagr * 100 if fr.oos else float("nan")
        lines.append(f"{i:>4} {cfg:>26} {fr.train.sharpe:>11.2f} "
                     f"{(fr.oos.sharpe if fr.oos else float('nan')):>9.2f} "
                     f"{oos_cagr:>9.1f} {(fr.oos.alpha if fr.oos else float('nan')):>9.1f}")
    return "\n".join(lines)


def build_curves(fold_results, runner):
    """Stitch adaptive + baseline OOS equity, and load the Nifty 50 benchmark over
    the same stitched span. Returns (adaptive_metrics, baseline_metrics, benchmark_metrics,
    adaptive_curve, baseline_curve, benchmark_curve)."""
    adaptive_curve = stitch([fr.oos.equity for fr in fold_results if fr.oos])
    baseline_curve = stitch([fr.baseline_oos.equity for fr in fold_results if fr.baseline_oos])
    first = fold_results[0].fold.test_start
    last = fold_results[-1].fold.test_end
    bench_raw = runner.load_close_series("Nifty 50", first, last)
    bench_curve = stitch([bench_raw]) if bench_raw else []
    return (curve_metrics(adaptive_curve), curve_metrics(baseline_curve),
            curve_metrics(bench_curve) if bench_curve else {"cagr": float("nan"), "sharpe": float("nan")},
            adaptive_curve, baseline_curve, bench_curve)


def save_plot(adaptive_curve, baseline_curve, bench_curve, path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 6), dpi=130)
    for curve, label in [(adaptive_curve, "adaptive (walk-forward)"),
                         (baseline_curve, "fixed N=20, 3m"),
                         (bench_curve, "Nifty 50")]:
        if curve:
            ax.plot([d for d, _ in curve], [v for _, v in curve], label=label)
    ax.set_title("Out-of-sample equity — adaptive vs fixed vs benchmark")
    ticks = ax.get_xticks()
    ax.legend(); ax.set_xticks(ticks[::max(1, len(ticks) // 8)])
    fig.autofmt_xdate(); fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
