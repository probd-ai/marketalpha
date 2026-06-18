from dataclasses import dataclass
from datetime import date
import pandas as pd


@dataclass
class SimResult:
    equity: pd.Series
    turnover: float
    holdings: list[tuple[date, dict[str, float]]]


def simulate(
    close_panel: pd.DataFrame,
    schedule: list[tuple[date, dict[str, float]]],
    start_capital: float,
    cost_bps: float,
) -> SimResult:
    schedule = sorted(schedule, key=lambda x: x[0])
    if not schedule:
        return SimResult(pd.Series(dtype=float), 0.0, [])

    start = schedule[0][0]
    prices = close_panel.loc[close_panel.index.date >= start].ffill()
    rebal: dict[date, dict[str, float]] = {d: w for d, w in schedule}

    cash: float = float(start_capital)
    shares: dict[str, float] = {}
    traded_notional: float = 0.0
    equity_points: dict[pd.Timestamp, float] = {}

    for ts in prices.index:
        d: date = ts.date()
        row: pd.Series = prices.loc[ts]

        if d in rebal:
            port_value: float = cash + sum(
                sh * float(row.get(s, 0.0) or 0.0) for s, sh in shares.items()
            )
            target: dict[str, float] = {s: w * port_value for s, w in rebal[d].items()}
            new_shares: dict[str, float] = {}
            for s, tv in target.items():
                px_val = row.get(s)
                if px_val is not None and not pd.isna(px_val) and float(px_val) > 0:
                    new_shares[s] = tv / float(px_val)

            for s in set(shares) | set(new_shares):
                px_val = row.get(s)
                px: float = float(px_val) if (px_val is not None and not pd.isna(px_val)) else 0.0
                delta: float = (new_shares.get(s, 0.0) - shares.get(s, 0.0)) * px
                traded_notional += abs(delta)
                cost: float = abs(delta) * cost_bps / 1e4
                cash -= delta + cost

            shares = new_shares

        equity_points[ts] = cash + sum(
            sh * float(row.get(s, 0.0) or 0.0) for s, sh in shares.items()
        )

    equity: pd.Series = pd.Series(equity_points)
    mean_equity: float = float(equity.mean()) if len(equity) else 0.0
    turnover: float = traded_notional / mean_equity if mean_equity else 0.0
    return SimResult(equity, turnover, list(schedule))
