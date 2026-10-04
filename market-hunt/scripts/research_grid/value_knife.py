"""Falling-knife stress test of the Grid-10 VALUE core, run on the production engine (fund.engine.grid10).

The worry: Grid-10 sells a holding ONLY when its grid turns down AND its 22-day return is positive. A stock that
keeps falling (22d return stays negative) is therefore never sold by the rule — only an index exit removes it — and
the weekly top-up to equity/N sends fresh money into it (averaging down). This script measures that risk directly.

Period: Oct 2018 -> Sep 2026, point-in-time NIFTY50. Membership = NSE month-end files (nifty50_mcwb/) before
Oct 2020 (covers the Yes Bank collapse and the Covid crash, never used to design the strategy) and the platform's
reconstitution data from Oct 2020. Weekly schedule = production (period_start_shifted weekly+k), k = 0..4.

1. Position anatomy (every episode incl. open ones): worst drawdown from cost, how it ended (bounce-sell / index exit
   / still open), rupee P&L as % of the sleeve, time underwater, and what the averaging-down top-ups earned.
2. Book-level drag: share of the sleeve sitting in positions >20% / >35% under cost, over time.
3. Crash windows: sleeve vs equal-weight PIT NIFTY50 vs Nifty 50.
4. Rule variants (sensitivity, NOT optimisation — each is one pre-declared alternative):
     base      production rule
     no_avg    never top up a holding that is below its average cost
     stop25    also sell a holding at -25% from average cost (22-session re-entry block)
     time26    also sell a holding held >= 26 weeks that is still below average cost (same block)
     griddown  sell on grid DOWN regardless of the 22d return (does not wait for the bounce)
5. Selection control: random 10 from the same grid-UP pool, same exits (20 seeds) — is "lowest 22d" doing work?

  .venv/Scripts/python.exe scripts/research_grid/value_knife.py
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from crash_test import build_intervals, load_prices, read_mcwb  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402

from fund.data import BENCH, charge_rates, load_truevx  # noqa: E402
from fund.engine.calendar import period_start_shifted, trading_days  # noqa: E402
from fund.engine.costs import Costs  # noqa: E402
from fund.engine.grid10 import Grid10, _nan  # noqa: E402
from fund.engine.market import Market  # noqa: E402
from fund.engine.replay import replay_sleeve  # noqa: E402
from fund.engine.types import Lot, Order, Reason  # noqa: E402

START, SWITCH, END = date(2018, 10, 1), date(2020, 10, 1), date(2026, 9, 25)
CAP, SLIP, N = 1_260_000, 0.0005, 10
WINDOWS = {
    "Full Oct 2018 - Sep 2026": ("2018-10-01", "2026-09-25"),
    "OOS Oct 2018 - Sep 2020": ("2018-10-01", "2020-09-30"),
    "Design Oct 2020 - Sep 2026": ("2020-10-01", "2026-09-25"),
}
CRASHES = {  # peak -> trough of the Nifty 50 (approximate calendar windows; DD measured inside)
    "Covid crash (Jan-Mar 2020)": ("2020-01-14", "2020-03-24"),
    "2022 H1 sell-off": ("2022-01-17", "2022-06-17"),
    "Sep 2024 - Mar 2025 correction": ("2024-09-26", "2025-03-04"),
}


# ------------------------------------------------------------------------------------------------ variants
class Grid10X(Grid10):
    """Grid10 with pre-declared exit / sizing alternatives. With defaults it must equal Grid10 exactly (asserted)."""

    def __init__(self, n=10, no_avg=False, stop=None, time_weeks=None, griddown=False, seed=None):
        super().__init__(n)
        self.no_avg, self.stop, self.time_weeks, self.griddown = no_avg, stop, time_weeks, griddown
        self.rng = np.random.default_rng(seed) if seed is not None else None

    def pick(self, m, prev, pool, held, st=None, i=None):
        cost = st.params.get("cost", {}) if st else {}
        opened = st.params.get("opened_i", {}) if st else {}
        block = st.params.get("block", {}) if st else {}
        sells = set()
        for s in held:
            if s not in pool:
                continue
            g, r = m.grid.at[prev, s], m.roc.at[prev, s]
            if g == -1 and not _nan(r) and (r > 0 or self.griddown):
                sells.add(s)
            px = m.cclose.at[prev, s]
            under = s in cost and px * st.qty(s) < cost[s]
            if self.stop and s in cost and px * st.qty(s) <= cost[s] * (1 - self.stop):
                sells.add(s); block[s] = i + 22
            if self.time_weeks and under and i - opened.get(s, i) >= self.time_weeks * 5:
                sells.add(s); block[s] = i + 22
        keep = [s for s in held if s not in sells]
        filtered = [s for s in pool if m.grid.at[prev, s] == 1 and not _nan(m.roc.at[prev, s]) and s not in keep
                    and block.get(s, -1) < (i or 0)]
        if self.rng is not None:
            cands = list(self.rng.permutation(sorted(filtered)))
        else:
            cands = list(m.roc.loc[prev, filtered].sort_values().index)
        return sorted(keep + cands[: max(0, self.n - len(keep))])

    def on_day(self, st, m, costs, cal, i, prev, rebalance):
        # identical to Grid10.on_day except: pick() sees the state, and no_avg zeroes gaps of holdings under cost
        d = cal[i]
        orders: list[Order] = []
        pend = st.params.setdefault("pending_exits", [])
        cost = st.params.setdefault("cost", {})
        opened = st.params.setdefault("opened_i", {})

        def sell(sym, px, reason):
            q = st.lots.pop(sym)[0].qty
            proceeds = costs.sell_proceeds(q, px)
            st.cash += proceeds
            cost.pop(sym, None); opened.pop(sym, None)
            orders.append(Order(sym, "SELL", q, float(px), proceeds, reason))

        def buy(sym, v, px, reason):
            q = costs.buy_qty(v, px)
            st.cash -= v
            old = st.lots.get(sym)
            st.lots[sym] = [Lot(sym, (old[0].qty if old else 0.0) + q, old[0].opened if old else d.date(), 0)]
            cost[sym] = cost.get(sym, 0.0) + v
            opened.setdefault(sym, i)
            orders.append(Order(sym, "BUY", q, float(px), v, reason))

        if i > 0:
            today_members = m.members(d.date())
            for s in st.lots:
                if s not in today_members and s not in pend:
                    pend.append(s)
        for sym in sorted(pend):
            if sym not in st.lots:
                pend.remove(sym)
                continue
            o = m.opens.at[d, sym]
            if _nan(o):
                lb = m.last_bar.get(sym)
                if lb is None or lb >= d:
                    continue
                o = m.closes.at[lb, sym]
            sell(sym, o, Reason.INDEX_EXIT)
            pend.remove(sym)

        if rebalance or i == 0:
            members = sorted(m.members(d.date()))
            pool = [s for s in members if s in m.roc.columns and not _nan(m.roc.at[prev, s])
                    and not _nan(m.opens.at[d, s])]
            held_before = set(st.lots)
            pick = self.pick(m, prev, pool, sorted(held_before), st, i)
            px_open = {s: (m.opens.at[d, s] if not _nan(m.opens.at[d, s]) else m.cclose.at[prev, s]) for s in st.lots}
            equity = st.cash + sum(l[0].qty * px_open[s] for s, l in st.lots.items())
            target = equity / max(len(pick), 1)
            for s in [s for s in st.lots if s not in pick]:
                if not _nan(m.opens.at[d, s]):
                    sell(s, m.opens.at[d, s], Reason.GRID_DOWN_POSITIVE_MONTH)
            tradable = [s for s in pick if not _nan(m.opens.at[d, s])]
            gaps = {s: max(target - (st.lots[s][0].qty if s in st.lots else 0.0) * m.opens.at[d, s], 0.0)
                    for s in tradable}
            if self.no_avg:
                for s in tradable:
                    if s in st.lots and st.lots[s][0].qty * m.opens.at[d, s] < cost.get(s, 0.0):
                        gaps[s] = 0.0
            total_gap = sum(gaps.values())
            budget = st.cash
            alloc = {s: (budget * g / total_gap if total_gap > budget else g) for s, g in gaps.items()} \
                if total_gap > 0 else {s: 0.0 for s in tradable}
            rest = budget - sum(alloc.values())
            if rest > 1e-6 and tradable:
                spread = [s for s in tradable if gaps[s] > 0 or not self.no_avg] or tradable
                for s in spread:
                    alloc[s] += rest / len(spread)
            for s, v in alloc.items():
                if v > 1e-6:
                    reason = (Reason.NEW_LOWEST_22D if s not in held_before
                              else Reason.TOPUP_TO_TARGET if gaps[s] > 0 else Reason.REMAINDER_SPREAD)
                    buy(s, v, m.opens.at[d, s], reason)
        return orders


VARIANTS = {"base": {}, "no_avg": {"no_avg": True}, "stop25": {"stop": 0.25}, "time26": {"time_weeks": 26},
            "griddown": {"griddown": True}}


# ------------------------------------------------------------------------------------------------ data
def load():
    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    mc_iv = build_intervals(read_mcwb(), START, date(2020, 9, 30))
    db_iv = load_membership(db, "NIFTY50", date.today())
    ever = sorted(union_over(mc_iv, START, date(2020, 9, 30)) | union_over(db_iv, SWITCH, END))
    members = lambda d: members_as_of(mc_iv, d) if d < SWITCH else members_as_of(db_iv, d)
    opens, closes = load_prices(db, ever)
    lo = pd.Timestamp("2018-01-01")
    opens, closes = opens.loc[lo: pd.Timestamp(END)], closes.loc[lo: pd.Timestamp(END)]
    cols = [c for c in closes.columns if c != BENCH]
    ml = load_truevx(db, cols, lo.to_pydatetime(), ("mean_long",))["mean_long"]
    from fund.engine.signals import grid_state, roc
    grid = grid_state(ml.reindex(index=closes.index, columns=cols))
    r = roc(closes[cols])
    m = Market(opens.reindex(columns=closes.columns), closes, BENCH, members, r, grid=grid)
    no_px = [s for s in ever if s not in closes.columns]
    no_ind = [s for s in cols if s not in ml.columns or ml[s].loc[: "2019-01-01"].dropna().empty]
    ew = ew_curve(closes, members)
    return m, ew, {"no_prices": no_px, "no_truevx_before_2019": no_ind, "ever_members": len(ever)}


def ew_curve(closes, members):
    rets = closes.drop(columns=[BENCH]).pct_change(fill_method=None)
    vals = []
    for d in rets.index:
        ms = [s for s in members(d.date()) if s in rets.columns]
        vals.append(rets.loc[d, ms].mean() if ms else np.nan)
    return (1 + pd.Series(vals, index=rets.index).fillna(0)).cumprod()


# ------------------------------------------------------------------------------------------------ analytics
def stats(s: pd.Series) -> dict:
    s = s.dropna()
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    r = s.pct_change().dropna()
    return {"cagr": (s.iloc[-1] / s.iloc[0]) ** (1 / yrs) - 1, "max_dd": float((s / s.cummax() - 1).min()),
            "sharpe": float(r.mean() / r.std() * math.sqrt(252))}


def window(s, a, b):
    w = s[(s.index >= a) & (s.index <= b)]
    return w / w.iloc[0]


def episodes(res, m: Market):
    """One row per holding episode (first buy -> full sell, or open at the end), marked daily."""
    cc = m.cclose
    rows, live = [], {}
    for d in sorted(res.orders):
        for o in res.orders[d]:
            e = live.get(o.symbol)
            if o.side == "BUY":
                if e is None:
                    e = live[o.symbol] = {"symbol": o.symbol, "entry": d, "entry_px": o.px, "invested": 0.0,
                                          "qty": 0.0, "buys": [], "sleeve_at_entry": float(res.equity.get(d, np.nan))}
                under = e["qty"] > 0 and e["qty"] * o.px < e["invested"]
                e["buys"].append((d, o.value, o.qty, o.reason.value if hasattr(o.reason, "value") else o.reason, under))
                e["invested"] += o.value
                e["qty"] += o.qty
            else:
                e = live.pop(o.symbol)
                e.update(exit=d, exit_px=o.px, proceeds=o.value,
                         how=o.reason.value if hasattr(o.reason, "value") else str(o.reason))
                rows.append(e)
    last = res.equity.index[-1]
    for s, e in live.items():
        e.update(exit=None, exit_px=float(cc.at[last, s]), proceeds=e["qty"] * float(cc.at[last, s]), how="OPEN")
        rows.append(e)
    out = []
    for e in rows:
        end = e["exit"] if e["exit"] is not None else last
        # daily mark / running cost (qty + invested step with each buy)
        path = cc.loc[e["entry"]: end, e["symbol"]]
        q = pd.Series(0.0, index=path.index); c = pd.Series(0.0, index=path.index)
        for (bd, v, bq, _, _) in e["buys"]:
            q.loc[bd:] += bq
            c.loc[bd:] += v
        mark = (q * path) / c
        ratio = mark - 1
        # what an averaging-down top-up turned into: its shares valued at the exit (or last) price
        topup_under = [(bd, v, bq) for (bd, v, bq, rsn, und) in e["buys"] if und]
        tu_ret = (sum(bq * e["exit_px"] for _, _, bq in topup_under) / sum(v for _, v, _ in topup_under) - 1) \
            if topup_under else np.nan
        out.append({
            "symbol": e["symbol"], "entry": e["entry"].date(), "exit": e["exit"].date() if e["exit"] is not None else None,
            "how": e["how"], "days": (end - e["entry"]).days, "invested": e["invested"], "proceeds": e["proceeds"],
            "pnl": e["proceeds"] - e["invested"], "ret": e["proceeds"] / e["invested"] - 1,
            "pnl_pct_sleeve": (e["proceeds"] - e["invested"]) / e["sleeve_at_entry"],
            "mae": float(ratio.min()), "days_under": int((ratio < 0).sum()),
            "longest_under_sessions": int(_longest(ratio < 0)),
            "n_topups_under": len(topup_under), "topup_under_rupees": sum(v for _, v, _ in topup_under),
            "topup_under_ret": tu_ret,
        })
    return pd.DataFrame(out)


def _longest(b: pd.Series) -> int:
    best = cur = 0
    for x in b:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    return best


def drag(res, m: Market, ep: pd.DataFrame):
    """Daily share of the sleeve held in positions marked >20% / >35% below their running cost."""
    cc = m.cclose
    idx = res.equity.index
    u20 = pd.Series(0.0, index=idx); u35 = pd.Series(0.0, index=idx); nb = pd.Series(0.0, index=idx)
    # rebuild qty / cost per symbol per day from orders
    qty, cost = {}, {}
    ords = {d: res.orders[d] for d in res.orders}
    for d in idx:
        for o in ords.get(d, []):
            if o.side == "BUY":
                qty[o.symbol] = qty.get(o.symbol, 0.0) + o.qty; cost[o.symbol] = cost.get(o.symbol, 0.0) + o.value
            else:
                qty.pop(o.symbol, None); cost.pop(o.symbol, None)
        eq = res.equity.at[d]
        for s, q in qty.items():
            v = q * cc.at[d, s]
            r = v / cost[s] - 1
            if r < -0.20:
                u20.at[d] += v / eq
            if r < -0.35:
                u35.at[d] += v / eq; nb.at[d] += 1
    return {"share_under20_mean": float(u20.mean()), "share_under20_max": float(u20.max()),
            "share_under20_max_date": str(u20.idxmax().date()),
            "share_under35_mean": float(u35.mean()), "share_under35_max": float(u35.max()),
            "days_with_any_under35": float((nb > 0).mean())}


# ------------------------------------------------------------------------------------------------ main
def main():
    m, ew, cov = load()
    costs = Costs(*charge_rates(), SLIP)
    cal = trading_days(m.closes[BENCH], START, END)
    bench = m.closes[BENCH].reindex(cal).ffill()
    ew = ew.reindex(cal).ffill()
    rep = {"coverage": cov, "controls": {}, "variants": {}, "crashes": {}, "anatomy": {}}
    for w, (a, b) in WINDOWS.items():
        rep["controls"][w] = {"ew50": stats(window(ew, a, b)), "nifty50": stats(window(bench, a, b))}

    runs = {}
    for name, kw in VARIANTS.items():
        for k in range(5):
            sched = period_start_shifted(cal, "weekly", k)
            res = replay_sleeve(Grid10X(N, **kw), m, costs, START, END, sched, CAP)
            runs[(name, k)] = res
            print(f"{name} k={k} {stats(res.equity)}", flush=True)
    # the variant harness with defaults must be the production engine, to the rupee
    prod = replay_sleeve(Grid10(N), m, costs, START, END, period_start_shifted(cal, "weekly", 0), CAP)
    gap = float((prod.equity - runs[("base", 0)].equity).abs().max())
    rep["base_vs_production_max_abs_gap_rupees"] = gap
    assert gap < 1e-6, gap

    for name in VARIANTS:
        rep["variants"][name] = {}
        for w, (a, b) in WINDOWS.items():
            ss = [stats(window(runs[(name, k)].equity, a, b)) for k in range(5)]
            rep["variants"][name][w] = {f: {"min": min(x[f] for x in ss), "median": float(np.median([x[f] for x in ss])),
                                            "max": max(x[f] for x in ss)} for f in ("cagr", "max_dd", "sharpe")}
        rep["crashes"][name] = {c: float(np.median([(window(runs[(name, k)].equity, a, b) /
                                                    window(runs[(name, k)].equity, a, b).cummax() - 1).min()
                                                   for k in range(5)])) for c, (a, b) in CRASHES.items()}
    for c, (a, b) in CRASHES.items():
        rep["crashes"].setdefault("ew50", {})[c] = float((window(ew, a, b) / window(ew, a, b).cummax() - 1).min())
        rep["crashes"].setdefault("nifty50", {})[c] = float((window(bench, a, b) / window(bench, a, b).cummax() - 1).min())

    # random-from-the-same-pool control (weekly k=0), same exits
    rnd = []
    for seed in range(20):
        res = replay_sleeve(Grid10X(N, seed=seed), m, costs, START, END, period_start_shifted(cal, "weekly", 0), CAP)
        rnd.append({w: stats(window(res.equity, a, b)) for w, (a, b) in WINDOWS.items()})
    rep["random_pool_control"] = {w: {f: {"p5": float(np.percentile([r[w][f] for r in rnd], 5)),
                                          "median": float(np.median([r[w][f] for r in rnd])),
                                          "p95": float(np.percentile([r[w][f] for r in rnd], 95))}
                                      for f in ("cagr", "max_dd", "sharpe")} for w in WINDOWS}

    # anatomy of the production rule, all five weekdays pooled
    eps = []
    for k in range(5):
        e = episodes(runs[("base", k)], m); e["k"] = k; eps.append(e)
    ep = pd.concat(eps, ignore_index=True)
    ep.to_csv(HERE / "value_knife_episodes.csv", index=False)
    e0 = ep[ep.k == 0]
    closed = e0[e0.how != "OPEN"]
    buckets = {}
    for lo_, hi_ in [(-0.10, 0.0), (-0.20, -0.10), (-0.35, -0.20), (-0.50, -0.35), (-1.0, -0.50)]:
        b = e0[(e0.mae <= hi_) & (e0.mae > lo_)]
        buckets[f"MAE {int(hi_*100)}..{int(lo_*100)}%"] = {
            "episodes": int(len(b)), "ended_positive": int((b.ret > 0).sum()),
            "ended_by": b.how.value_counts().to_dict(), "median_final_ret": float(b.ret.median()) if len(b) else None,
            "total_pnl_pct_sleeve": float(b.pnl_pct_sleeve.sum())}
    tu = e0[e0.n_topups_under > 0]
    rep["anatomy"] = {
        "episodes_k0": int(len(e0)), "closed": int(len(closed)), "open_at_end": int((e0.how == "OPEN").sum()),
        "closed_win_rate": float((closed.ret > 0).mean()), "all_incl_open_win_rate": float((e0.ret > 0).mean()),
        "exit_reasons": e0.how.value_counts().to_dict(),
        "mae_buckets": buckets,
        "worst_15": e0.nsmallest(15, "mae")[["symbol", "entry", "exit", "how", "days", "mae", "ret", "pnl_pct_sleeve",
                                              "longest_under_sessions", "n_topups_under", "topup_under_ret"]]
        .to_dict("records"),
        "worst_pnl_10": e0.nsmallest(10, "pnl_pct_sleeve")[["symbol", "entry", "exit", "how", "mae", "ret",
                                                              "pnl_pct_sleeve"]].to_dict("records"),
        "best_pnl_10": e0.nlargest(10, "pnl_pct_sleeve")[["symbol", "entry", "exit", "how", "ret", "pnl_pct_sleeve"]]
        .to_dict("records"),
        "averaging_down": {"episodes_with_topup_under_cost": int(len(tu)),
                           "rupees": float(tu.topup_under_rupees.sum()),
                           "rupee_weighted_ret_of_those_topups": float((tu.topup_under_rupees * tu.topup_under_ret).sum()
                                                                       / tu.topup_under_rupees.sum()) if len(tu) else None,
                           "share_of_topups_that_made_money": float((tu.topup_under_ret > 0).mean()) if len(tu) else None},
        "longest_underwater_sessions": int(e0.longest_under_sessions.max()),
        "drag": drag(runs[("base", 0)], m, e0),
        "pooled_k0_4": {"episodes": int(len(ep)), "mae_le_-35": int((ep.mae <= -0.35).sum()),
                        "mae_le_-35_ended_positive": int(((ep.mae <= -0.35) & (ep.ret > 0)).sum()),
                        "worst_single_pnl_pct_sleeve": float(ep.pnl_pct_sleeve.min())},
    }
    (HERE / "value_knife.json").write_text(json.dumps(rep, indent=2, default=str))
    print(json.dumps({k: rep[k] for k in ("coverage", "base_vs_production_max_abs_gap_rupees")}, default=str))
    print("done")


if __name__ == "__main__":
    main()
