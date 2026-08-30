from dataclasses import dataclass
from datetime import datetime
import math
from walkforward.folds import Fold
from walkforward.sim_runner import RunResult


@dataclass
class FoldResult:
    fold: Fold
    best_config: tuple[int, str]
    train: RunResult
    oos: RunResult
    baseline_oos: RunResult


def select_best(results: dict[tuple[int, str], RunResult], objective: str = "sharpe") -> tuple[int, str]:
    valid = {k: v for k, v in results.items() if v is not None}
    if not valid:
        raise ValueError("no valid configs to select from")
    return max(valid, key=lambda k: getattr(valid[k], objective))


def curve_metrics(equity: list[tuple[str, float]]) -> dict:
    vals = [v for _, v in equity]
    dates = [datetime.strptime(d, "%Y-%m-%d") for d, _ in equity]
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    if rets:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / len(rets)
        std = math.sqrt(var)
        sharpe = (mean / std * math.sqrt(252)) if std > 0 else float("nan")
    else:
        sharpe = float("nan")
    years = max((dates[-1] - dates[0]).days / 365.25, 1e-9) if len(dates) >= 2 else 1e-9
    cagr = (vals[-1] / vals[0]) ** (1 / years) - 1 if len(vals) >= 2 else float("nan")
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {"cagr": cagr, "sharpe": sharpe, "max_drawdown": mdd * 100,
            "total_return": (vals[-1] / vals[0] - 1) * 100 if len(vals) >= 2 else float("nan")}


def stitch(segments: list[list[tuple[str, float]]], base: float = 100000.0) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    level = base
    for seg in segments:
        if not seg:
            continue
        seg_start = seg[0][1]
        for d, v in seg:
            out.append((d, level * v / seg_start))
        level = level * seg[-1][1] / seg_start
    return out


async def run_walk_forward(runner, folds, sizes, windows, baseline, objective="sharpe") -> list[FoldResult]:
    results: list[FoldResult] = []
    for fold in folds:
        grid: dict[tuple[int, str], RunResult] = {}
        for n in sizes:
            for w in windows:
                grid[(n, w)] = await runner.run(fold.train_start, fold.train_end, n, w)
        best = select_best(grid, objective)
        train_best = grid[best]
        oos = await runner.run(fold.test_start, fold.test_end, best[0], best[1])
        base_oos = await runner.run(fold.test_start, fold.test_end, baseline[0], baseline[1])
        results.append(FoldResult(fold, best, train_best, oos, base_oos))
    return results
