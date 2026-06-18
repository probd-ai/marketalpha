import numpy as np
import pandas as pd


def _total_return(equity: pd.Series) -> float:
    if len(equity) < 2 or equity.iloc[0] == 0:
        return 0.0
    return float(equity.iloc[-1] / equity.iloc[0] - 1)


def compute_metrics(
    equity: pd.Series,
    benchmark: pd.Series,
    turnover: float,
    periods_per_year: int = 252,
) -> dict:
    tr = _total_return(equity)
    btr = _total_return(benchmark)
    if len(equity) < 2:
        return {
            "total_return": tr,
            "cagr": 0.0,
            "volatility": 0.0,
            "sharpe": 0.0,
            "sortino": 0.0,
            "max_drawdown": 0.0,
            "turnover": float(turnover),
            "benchmark_total_return": btr,
            "alpha": tr - btr,
        }

    rets = equity.pct_change().dropna()
    years = len(equity) / periods_per_year
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
    vol = rets.std(ddof=0) * np.sqrt(periods_per_year)
    sharpe = (
        (rets.mean() / rets.std(ddof=0) * np.sqrt(periods_per_year))
        if rets.std(ddof=0)
        else 0.0
    )
    downside = rets[rets < 0].std(ddof=0)
    sortino = (
        (rets.mean() / downside * np.sqrt(periods_per_year)) if downside else 0.0
    )
    cummax = equity.cummax()
    max_dd = float(((equity - cummax) / cummax).min())

    return {
        "total_return": tr,
        "cagr": float(cagr),
        "volatility": float(vol),
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_drawdown": max_dd,
        "turnover": float(turnover),
        "benchmark_total_return": btr,
        "alpha": tr - btr,
    }
