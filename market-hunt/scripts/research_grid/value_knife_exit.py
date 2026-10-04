"""Follow-up to value_knife.py: sell IMMEDIATELY when the grid turns down (no waiting for a positive month), so a
falling stock is never held; entry = grid-UP names ranked by 22-day return, top 10 by LOWEST (value) and by HIGHEST
(momentum). Same engine, membership, costs, weekdays and windows as value_knife.py.

  .venv/Scripts/python.exe scripts/research_grid/value_knife_exit.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from value_knife import (CAP, CRASHES, END, N, START, WINDOWS, Grid10X, _nan, episodes, load,  # noqa: E402
                         stats, window)

from fund.data import BENCH, charge_rates  # noqa: E402
from fund.engine.calendar import period_start_shifted, trading_days  # noqa: E402
from fund.engine.costs import Costs  # noqa: E402
from fund.engine.replay import replay_sleeve  # noqa: E402

SLIP = 0.0005


class HighRoc(Grid10X):
    """griddown exit, but fill with the HIGHEST 22-day returns in grid-UP state."""

    def pick(self, m, prev, pool, held, st=None, i=None):
        sells = {s for s in held if s in pool and m.grid.at[prev, s] == -1 and not _nan(m.roc.at[prev, s])}
        keep = [s for s in held if s not in sells]
        filtered = [s for s in pool if m.grid.at[prev, s] == 1 and not _nan(m.roc.at[prev, s]) and s not in keep]
        cands = list(m.roc.loc[prev, filtered].sort_values(ascending=False).index)
        return sorted(keep + cands[: max(0, self.n - len(keep))])


def main():
    m, ew, cov = load()
    costs = Costs(*charge_rates(), SLIP)
    cal = trading_days(m.closes[BENCH], START, END)
    books = {"base (lowest ROC, sell on grid-down + up-month)": lambda: Grid10X(N),
             "lowest ROC, sell on grid-down": lambda: Grid10X(N, griddown=True),
             "highest ROC, sell on grid-down": lambda: HighRoc(N)}
    rep = {}
    for name, mk in books.items():
        runs = [replay_sleeve(mk(), m, costs, START, END, period_start_shifted(cal, "weekly", k), CAP)
                for k in range(5)]
        r = {"windows": {}, "crashes": {}}
        for w, (a, b) in WINDOWS.items():
            ss = [stats(window(x.equity, a, b)) for x in runs]
            r["windows"][w] = {f: [min(s[f] for s in ss), float(np.median([s[f] for s in ss])), max(s[f] for s in ss)]
                               for f in ("cagr", "max_dd", "sharpe")}
        for c, (a, b) in CRASHES.items():
            r["crashes"][c] = float(np.median([(window(x.equity, a, b) / window(x.equity, a, b).cummax() - 1).min()
                                               for x in runs]))
        ep = episodes(runs[0], m)
        yrs = (runs[0].equity.index[-1] - runs[0].equity.index[0]).days / 365.25
        closed = ep[ep.how != "OPEN"]
        r["anatomy_k0"] = {"episodes": int(len(ep)), "trades_per_year": len(ep) / yrs,
                           "median_hold_days": float(ep.days.median()), "win_rate": float((closed.ret > 0).mean()),
                           "mae_le_-20": int((ep.mae <= -0.20).sum()), "mae_le_-35": int((ep.mae <= -0.35).sum()),
                           "worst_mae": float(ep.mae.min()), "worst_pnl_pct_sleeve": float(ep.pnl_pct_sleeve.min()),
                           "worst_names": ep.nsmallest(5, "pnl_pct_sleeve")[["symbol", "entry", "exit", "mae", "ret"]]
                           .to_dict("records")}
        rep[name] = r
        print(name, {w: [round(x * 100, 1) for x in v["cagr"]] for w, v in r["windows"].items()}, flush=True)
    (HERE / "value_knife_exit.json").write_text(json.dumps(rep, indent=2, default=str))
    print("done")


if __name__ == "__main__":
    main()
