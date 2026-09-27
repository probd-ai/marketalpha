"""TrueVX grid mean-reversion study on NIFTY50, point-in-time.

Rules (per stock, per grid level k in 10..90):
  --direction down (mean reversion, default):
  * ENTRY  : truevx_score crosses DOWN through k at a close (prev >= k > today)
             -> buy one tranche tagged k at the next session's open,
             unless a tranche tagged k is already open for that stock.
  * EXIT   : an open tranche k is sold at the next open once the close score > k+offset.
             With offset 10, tranche 90 has no exit level -> held until the stock leaves the index.
  --direction up (momentum): mirror — buy on cross UP through k (prev < k <= today),
             sell once score < k-offset (offset 10: tranche 10 never sells; offset 0: exit
             when the score falls back through its own entry level).
  * INDEX  : entries only while the stock is a point-in-time NIFTY50 member; every open
             tranche is force-exited when it leaves the index.

Sizing: fixed rupee tranche = capital / (50 * 9); cash can never run out, so there is no
capital-rationing artefact. Costs: statutory delivery charges from BrokerageCalculator on
both sides + slippage bps on both sides. Fills at next open (no look-ahead).

Controls:
  * Nifty 50 buy & hold.
  * Exposure-matched Nifty: the same fraction of equity the strategy has deployed each day,
    invested in Nifty 50 instead -> isolates what the grid adds over "being in the market".

Usage:
  .venv/Scripts/python.exe scripts/research_grid/grid_meanrev.py [--slip-bps 5] [--out results.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from brokerage_calculator import BrokerageCalculator  # noqa: E402
from membership import load_membership, members_as_of, union_over  # noqa: E402

INDEX = "NIFTY50"
BENCH = "Nifty 50"
LEVELS = list(range(10, 100, 10))
SLOTS = 50 * len(LEVELS)
LOAD_FROM = datetime(2020, 6, 1)          # indicator/price history needed before the first signal

WINDOWS = {
    "full": (date(2020, 10, 1), None),
    "P1":   (date(2020, 10, 1), date(2023, 6, 30)),
    "P2":   (date(2023, 7, 1), None),
}


def charge_rates() -> tuple[float, float]:
    bc = BrokerageCalculator()
    v = 1_000_000.0
    buy = bc.calculate_transaction_charges(v, "BUY").total_charges / v
    sell = bc.calculate_transaction_charges(v, "SELL").total_charges / v
    return buy, sell


def load_data(db, symbols: list[str], field: str = "truevx_score"):
    pcs = [c for c in db.list_collection_names() if c.startswith("prices_") and c >= "prices_2020"]
    rows = []
    for c in pcs:
        rows += list(db[c].find(
            {"symbol": {"$in": symbols + [BENCH]}, "date": {"$gte": LOAD_FROM}},
            {"_id": 0, "symbol": 1, "date": 1, "open_price": 1, "close_price": 1},
        ))
    px = pd.DataFrame(rows)
    px["date"] = pd.to_datetime(px["date"]).dt.normalize()
    px = px.drop_duplicates(["symbol", "date"])
    opens = px.pivot(index="date", columns="symbol", values="open_price").sort_index()
    closes = px.pivot(index="date", columns="symbol", values="close_price").sort_index()

    ind = list(db.indicators.find(
        {"symbol": {"$in": symbols}, "indicator_type": "truevx", "base_symbol": BENCH,
         "date": {"$gte": LOAD_FROM}},
        {"_id": 0, "symbol": 1, "date": 1, f"data.{field}": 1},
    ))
    sc = pd.DataFrame([{"symbol": d["symbol"], "date": d["date"],
                        "s": (d.get("data") or {}).get(field)} for d in ind])
    sc["date"] = pd.to_datetime(sc["date"]).dt.normalize()
    score = sc.drop_duplicates(["symbol", "date"]).pivot(index="date", columns="symbol", values="s").sort_index()
    return opens, closes, score


@dataclass
class Tranche:
    symbol: str
    level: int
    signal_date: str
    entry_date: str
    entry_px: float
    qty: float
    cost_in: float                 # rupees paid incl. charges + slippage
    bench_in: float
    exit_date: str | None = None
    exit_px: float | None = None
    proceeds: float | None = None
    bench_out: float | None = None
    reason: str | None = None      # grid | index_exit | open


def exit_hit(direction: str, level: int, offset: int, s: float) -> bool:
    """down-grid: sell once score > level+offset (no exit if that is >= 100).
    up-grid:   sell once score < level-offset (no exit if that is <= 0)."""
    if direction == "down":
        lim = level + offset
        return lim < 100 and s > lim
    lim = level - offset
    return lim > 0 and s < lim


def entry_hit(direction: str, level: int, p: float, s: float) -> bool:
    return p >= level > s if direction == "down" else p < level <= s


def run(opens, closes, score, intervals, start: date, end: date | None,
        capital: float, slip: float, buy_rate: float, sell_rate: float,
        direction: str = "down", exit_offset: int = 10):
    cal = closes.index[(closes.index >= pd.Timestamp(start)) & closes[BENCH].notna()]
    if end is not None:
        cal = cal[cal <= pd.Timestamp(end)]
    slot = capital / SLOTS

    last_bar = {s: closes[s].last_valid_index() for s in closes.columns}
    cclose = closes.ffill()                              # valuation only; fills need a real bar
    cash = capital
    open_tr: dict[tuple[str, int], Tranche] = {}
    done: list[Tranche] = []
    pend_buy: list[tuple[str, int, str]] = []            # (symbol, level, signal_date)
    pend_sell: list[tuple[tuple[str, int], str]] = []    # (key, reason)
    eq_rows = []

    def member(d) -> set[str]:
        return members_as_of(intervals, d.date())

    for i, d in enumerate(cal):
        # ---- 1) fills at this session's open ------------------------------------------
        keep_sell = []
        for key, reason in pend_sell:
            tr = open_tr.get(key)
            if tr is None:
                continue
            o = opens.at[d, tr.symbol] if tr.symbol in opens.columns else np.nan
            if pd.isna(o):
                lb = last_bar.get(tr.symbol)
                if lb is not None and lb < d:            # no bar will ever come (delisted/merged)
                    px, when = closes.at[lb, tr.symbol], lb
                else:
                    keep_sell.append((key, reason))      # halted today; retry next session
                    continue
            else:
                px, when = o, d
            gross = tr.qty * px * (1 - slip)
            tr.proceeds = gross * (1 - sell_rate)
            tr.exit_px, tr.exit_date, tr.reason = float(px), str(when.date()), reason
            tr.bench_out = float(opens.at[d, BENCH]) if not pd.isna(opens.at[d, BENCH]) else float(cclose.at[d, BENCH])
            cash += tr.proceeds
            done.append(open_tr.pop(key))
        pend_sell = keep_sell

        today_members = member(d)
        keep_buy = []
        for sym, lvl, sig in pend_buy:
            if (sym, lvl) in open_tr or sym not in today_members:
                continue
            o = opens.at[d, sym] if sym in opens.columns else np.nan
            if pd.isna(o):
                if last_bar.get(sym) is not None and last_bar[sym] >= d:
                    keep_buy.append((sym, lvl, sig))     # halted today; retry
                continue
            px = o * (1 + slip)
            qty = slot / (px * (1 + buy_rate))
            cash -= slot
            bo = opens.at[d, BENCH]
            open_tr[(sym, lvl)] = Tranche(sym, lvl, sig, str(d.date()), float(o), qty, slot,
                                          float(bo if not pd.isna(bo) else cclose.at[d, BENCH]))
        pend_buy = keep_buy

        # ---- 2) mark to market at close -------------------------------------------------
        deployed = sum(t.qty * cclose.at[d, t.symbol] for t in open_tr.values())
        eq_rows.append((d, cash + deployed, deployed, len(open_tr), cclose.at[d, BENCH]))

        # ---- 3) signals at close -> orders for next open --------------------------------
        if i + 1 >= len(cal):
            break
        nxt = cal[i + 1]
        next_members = member(nxt)
        queued_sell = {k for k, _ in pend_sell}
        for key, tr in open_tr.items():
            if key in queued_sell:
                continue
            if tr.symbol not in next_members:
                pend_sell.append((key, "index_exit"))
                continue
            s = score.at[d, tr.symbol] if (d in score.index and tr.symbol in score.columns) else np.nan
            if not pd.isna(s) and exit_hit(direction, tr.level, exit_offset, s):
                pend_sell.append((key, "grid"))

        pos = score.index.get_loc(d) if d in score.index else None
        if pos is None or pos == 0:
            continue
        prev_row, row = score.iloc[pos - 1], score.iloc[pos]
        for sym in next_members:
            if sym not in score.columns:
                continue
            p, s = prev_row[sym], row[sym]
            if pd.isna(p) or pd.isna(s):
                continue
            for k in LEVELS:
                if entry_hit(direction, k, p, s) and (sym, k) not in open_tr:
                    pend_buy.append((sym, k, str(d.date())))

    # close the book at the last mark for reporting (not traded)
    last = cal[-1]
    for tr in open_tr.values():
        tr.exit_date, tr.exit_px = str(last.date()), float(cclose.at[last, tr.symbol])
        tr.proceeds = tr.qty * tr.exit_px              # unrealised, pre-cost
        tr.bench_out, tr.reason = float(cclose.at[last, BENCH]), "open"
    eq = pd.DataFrame(eq_rows, columns=["date", "equity", "deployed", "n_open", "bench"]).set_index("date")
    return eq, done + list(open_tr.values())


def cagr(series: pd.Series) -> float:
    yrs = (series.index[-1] - series.index[0]).days / 365.25
    return (series.iloc[-1] / series.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else float("nan")


def max_dd(series: pd.Series) -> float:
    return float((series / series.cummax() - 1).min())


def sharpe(series: pd.Series) -> float:
    r = series.pct_change().dropna()
    return float(r.mean() / r.std() * math.sqrt(252)) if r.std() > 0 else float("nan")


def ew_index(closes: pd.DataFrame, intervals) -> pd.Series:
    """Equal-weight, daily-rebalanced, point-in-time NIFTY50 (close-to-close)."""
    rets = closes.drop(columns=[BENCH]).pct_change(fill_method=None)
    vals = []
    for d in rets.index:
        m = [s for s in members_as_of(intervals, d.date()) if s in rets.columns]
        vals.append(rets.loc[d, m].mean() if m else np.nan)
    return (1 + pd.Series(vals, index=rets.index).fillna(0)).cumprod()


def summarize(eq: pd.DataFrame, trades: list[Tranche], capital: float, ew: pd.Series) -> dict:
    util = (eq["deployed"] / eq["equity"]).shift(1).fillna(0)
    bench_r = eq["bench"].pct_change().fillna(0)
    matched = capital * (1 + util * bench_r).cumprod()
    ew = ew.reindex(eq.index).ffill()
    ew_r = ew.pct_change().fillna(0)
    matched_ew = capital * (1 + util * ew_r).cumprod()
    ew_bh = capital * ew / ew.iloc[0]
    bh = capital * eq["bench"] / eq["bench"].iloc[0]
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    pnl = eq["equity"].iloc[-1] - capital
    avg_dep = float(eq["deployed"].mean())

    tdf = pd.DataFrame([asdict(t) for t in trades])
    out = {
        "period": f"{eq.index[0].date()} -> {eq.index[-1].date()}",
        "strategy": {"total_return": eq["equity"].iloc[-1] / capital - 1, "cagr": cagr(eq["equity"]),
                     "max_dd": max_dd(eq["equity"]), "sharpe": sharpe(eq["equity"])},
        "exposure_matched_nifty": {"total_return": matched.iloc[-1] / capital - 1, "cagr": cagr(matched),
                                   "max_dd": max_dd(matched), "sharpe": sharpe(matched)},
        "nifty_buy_hold": {"total_return": bh.iloc[-1] / capital - 1, "cagr": cagr(bh),
                           "max_dd": max_dd(bh), "sharpe": sharpe(bh)},
        "exposure_matched_ew50": {"total_return": matched_ew.iloc[-1] / capital - 1, "cagr": cagr(matched_ew),
                                  "max_dd": max_dd(matched_ew), "sharpe": sharpe(matched_ew)},
        "ew50_buy_hold": {"total_return": ew_bh.iloc[-1] / capital - 1, "cagr": cagr(ew_bh),
                          "max_dd": max_dd(ew_bh), "sharpe": sharpe(ew_bh)},
        "avg_utilisation": float((eq["deployed"] / eq["equity"]).mean()),
        "max_utilisation": float((eq["deployed"] / eq["equity"]).max()),
        "return_on_avg_deployed_pa": (pnl / avg_dep) / yrs if avg_dep > 0 else None,
        "tranches": int(len(tdf)),
    }
    if len(tdf):
        tdf["ret"] = tdf["proceeds"] / tdf["cost_in"] - 1
        tdf["bench_ret"] = tdf["bench_out"] / tdf["bench_in"] - 1
        tdf["alpha"] = tdf["ret"] - tdf["bench_ret"]
        # EW universe over the same window: signal close (≈ entry open) -> exit date
        tdf["ew_ret"] = [float(ew.asof(pd.Timestamp(x)) / ew.asof(pd.Timestamp(y)) - 1)
                         for x, y in zip(tdf["exit_date"], tdf["signal_date"])]
        tdf["alpha_ew"] = tdf["ret"] - tdf["ew_ret"]
        tdf["days"] = (pd.to_datetime(tdf["exit_date"]) - pd.to_datetime(tdf["entry_date"])).dt.days
        by = []
        for k, g in tdf.groupby("level"):
            closed = g[g["reason"] != "open"]
            op = g[g["reason"] == "open"]
            by.append({"level": int(k), "n": int(len(g)), "closed": int(len(closed)),
                       "win_rate_closed": float((closed["ret"] > 0).mean()) if len(closed) else None,
                       "avg_ret_closed": float(closed["ret"].mean()) if len(closed) else None,
                       "avg_alpha_closed": float(closed["alpha"].mean()) if len(closed) else None,
                       "avg_alpha_ew_closed": float(closed["alpha_ew"].mean()) if len(closed) else None,
                       "avg_alpha_ew_all": float(g["alpha_ew"].mean()),
                       "median_days_closed": float(closed["days"].median()) if len(closed) else None,
                       "open": int(len(op)),
                       "avg_ret_open": float(op["ret"].mean()) if len(op) else None,
                       "pnl_rupees": float((g["proceeds"] - g["cost_in"]).sum())})
        out["by_level"] = by
        out["exit_reasons"] = tdf["reason"].value_counts().to_dict()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=4_500_000)
    ap.add_argument("--slip-bps", type=float, default=5.0)
    ap.add_argument("--direction", choices=["down", "up"], default="down",
                    help="down: buy on cross-down through a level (mean reversion); up: buy on cross-up (momentum)")
    ap.add_argument("--exit-offset", type=int, default=10,
                    help="zones between entry level and exit level (10 = one zone, 0 = exit back through entry)")
    ap.add_argument("--out", default=str(Path(__file__).with_name("results.json")))
    args = ap.parse_args()

    db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
    intervals = load_membership(db, INDEX, date.today())
    ever = sorted(union_over(intervals, WINDOWS["full"][0], date.today()))
    opens, closes, score = load_data(db, ever)
    missing_px = [s for s in ever if s not in closes.columns]
    missing_sc = [s for s in ever if s not in score.columns]
    buy_rate, sell_rate = charge_rates()
    slip = args.slip_bps / 10_000

    report = {"universe": INDEX, "ever_members": len(ever), "missing_prices": missing_px,
              "missing_scores": missing_sc, "score_last_date": str(score.index.max().date()),
              "price_last_date": str(closes[BENCH].last_valid_index().date()),
              "direction": args.direction, "exit_offset": args.exit_offset,
              "buy_charge_rate": buy_rate, "sell_charge_rate": sell_rate, "slip_bps": args.slip_bps,
              "windows": {}}
    ew = ew_index(closes, intervals)
    for name, (s, e) in WINDOWS.items():
        eq, trades = run(opens, closes, score, intervals, s, e, args.capital, slip, buy_rate, sell_rate,
                         args.direction, args.exit_offset)
        report["windows"][name] = summarize(eq, trades, args.capital, ew)
        if name == "full":
            stem = Path(args.out).stem
            eq.to_csv(Path(args.out).with_name(f"{stem}_equity_full.csv"))
            pd.DataFrame([asdict(t) for t in trades]).to_csv(Path(args.out).with_name(f"{stem}_tranches_full.csv"), index=False)

    Path(args.out).write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
