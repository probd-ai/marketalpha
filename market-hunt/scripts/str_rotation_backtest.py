"""STR-gated accumulation: enter new names before they run, exit them as strength fades.

The Alpha strategy re-selects a top-N book every rebalance and holds whatever it picks,
regardless of how far a name has already travelled. That is why CUPID -- up 724% over a
year -- can enter at 2.4x even weight.

This tests a different use of the same selection. `STR` is the weighted mean of the
1M/3M/1Y returns (0.5/1.0/1.5, the same shape TrueVX gives its short/mid/long legs), so
it measures how far a name has ALREADY run. The project's own regime study found that
within already-strong names the most-rising quintile returned least (1.52%/mo vs 2.09%
for the most-fading, slope -0.196%/mo per SD, t=-1.96) and concluded it was "an avoid
rule, not a buy rule". Nothing has ever used it as one.

  ENTRY   only NEW entrants to the Alpha selection are eligible -- names the strategy
          just started wanting. Of those, take the LOWEST STR: picked, but not yet run.
  EXIT    the holdings whose STR FELL most since the last rebalance. Not lowest level
          (that is what we buy) -- lowest CHANGE. Strength fading, not strength absent.
  CAP     accumulate to `MAX_HOLD`, then swap EXIT_N out / BUY_N in each rebalance.

Control is the real portfolio: plain Alpha top-20, re-selected each rebalance.

    .venv/Scripts/python.exe scripts/str_rotation_backtest.py
"""
import itertools
import os
import sys
from datetime import date, datetime

import numpy as np
import pandas as pd
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membership import load_membership, members_as_of  # noqa: E402

UNIVERSE = "NIFTY500MICRO750"
START = datetime(2020, 6, 1)          # warm-up: STR needs 252 sessions
SNAP_FROM = date(2022, 1, 1)
THRESH = {"truevx_score": 90.0, "mean_short": 80.0, "mean_mid": 60.0, "mean_long": 50.0}
TOP_N = 20                            # the Alpha selection each rebalance
SEED_N, BUY_N, EXIT_N, MAX_HOLD = 10, 5, 5, 30
RANK_SESSIONS = 20                    # the 20-day-return ranker
STR_WINDOWS, STR_WEIGHTS = (22, 66, 252), (0.5, 1.0, 1.5)
BASKET_TARGET = 0.03      # exit batch must average >= +3% realised

BUY = 0.001 + 0.0000297 + 0.000001 + 0.00015 + 0.18 * (0.0000297 + 0.000001)
SELL = 0.001 + 0.0000297 + 0.000001 + 0.18 * (0.0000297 + 0.000001)

db = MongoClient("mongodb://localhost:27017/")["market_hunt"]
PRICE_COLLS = ("prices_2020_2024", "prices_2025_2029")


def close_series(symbol):
    rows = []
    for c in PRICE_COLLS:
        rows += list(db[c].find({"symbol": symbol, "date": {"$gte": START}},
                                {"_id": 0, "date": 1, "close_price": 1}))
    if not rows:
        return None
    s = pd.DataFrame(rows)
    s["date"] = pd.to_datetime(s["date"]).dt.normalize()
    s = s.dropna(subset=["close_price"]).drop_duplicates("date").sort_values("date")
    return s.set_index("date")["close_price"].astype(float)


print(f"loading {UNIVERSE} ...", flush=True)
intervals = load_membership(db, UNIVERSE, anchor=date.today())
ever = sorted({iv.symbol for iv in intervals})
closes, inds = {}, {}
for sym in ever:
    px = close_series(sym)
    if px is None or len(px) < 120:
        continue
    rows = list(db["indicators"].find(
        {"symbol": sym, "indicator_type": "truevx", "date": {"$gte": START}},
        {"_id": 0, "date": 1, "data": 1}))
    if not rows:
        continue
    idx = [pd.to_datetime(r["date"]).normalize() for r in rows]
    inds[sym] = pd.DataFrame([{k: r["data"].get(k) for k in THRESH} for r in rows],
                             index=idx).sort_index()
    closes[sym] = px
print(f"  {len(closes)} symbols\n", flush=True)

cal = close_series("RELIANCE")


def quarter_snaps(which):
    g = pd.Series(cal.index).groupby([cal.index.year, cal.index.quarter])
    days = g.min() if which == "first" else g.apply(lambda s: s.iloc[len(s) // 2])
    return [d for d in sorted(pd.to_datetime(days).tolist()) if d.date() >= SNAP_FROM]


def strength(sym, on):
    """Weighted mean of trailing 22/66/252-session returns as of `on`. None if short."""
    h = closes[sym][closes[sym].index <= on]
    if len(h) <= STR_WINDOWS[0] + 1:
        return None
    last, acc, wsum = h.iloc[-1], 0.0, 0.0
    for n, w in zip(STR_WINDOWS, STR_WEIGHTS):
        if len(h) > n and h.iloc[-1 - n] > 0:
            acc += (last / h.iloc[-1 - n] - 1) * 100 * w
            wsum += w
    return acc / wsum if wsum else None


def alpha_selection(on, i, ind_at, px_at, top_n=TOP_N):
    """The strategy's top-N: Alpha gate, then best 20-session return."""
    pool = []
    for s in members_as_of(intervals, on.date()):
        if s not in closes:
            continue
        r = ind_at[s].iloc[i]
        if any(pd.isna(r.get(k)) or r.get(k) is None or r[k] <= v for k, v in THRESH.items()):
            continue
        p0, p1 = px_at[s].iloc[i], px_at[s].iloc[i + 1]
        if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
            continue
        h = closes[s][closes[s].index <= on]
        if len(h) <= RANK_SESSIONS:
            continue
        pool.append((s, h.iloc[-1] / h.iloc[-1 - RANK_SESSIONS] - 1))
    pool.sort(key=lambda x: -x[1])
    return [s for s, _ in pool[:top_n]]


def run_sleeve(which, mode, seed=0):
    """Arms, each isolating one ingredient of STR-ROTATE:

      str_rotate    lowest-STR entry from new entrants, fading-STR exit  (the design)
      random_entry  RANDOM 5 from the same new entrants                  -> does STR
                    selection matter at all, or is the packaging doing the work?
      exit_oldest   lowest-STR entry, but exit the 5 held longest        -> does the
                    fading-STR exit matter, or would any rotation do?
      ew30          plain Alpha top-30, re-selected every rebalance      -> isolates
                    diversification (27 names vs 20) from any signal
      control       plain Alpha top-20 -- the live portfolio

      vintage_pnl   VINTAGE rotation. Buy the strategy's top 5 each rebalance, then
                    exit 2 from the cohort bought TWO rebalances ago (matured -- take
                    the winners) and 3 from the cohort bought LAST (recent -- cut the
                    losers). Every name lives at most 2 rebalances, so the book settles
                    at 7: the current cohort plus 2 survivors. Ranked by return since
                    entry.
      vintage_str   Same vintage mechanics, ranked by STR level instead of P&L.
    """
    import random as _random
    rng = _random.Random(seed)
    snaps = quarter_snaps(which)
    ind_at = {s: inds[s].reindex(snaps, method="ffill") for s in closes}
    px_at = {s: closes[s].reindex(snaps, method="ffill") for s in closes}

    eq, prev_w = [1.0], {}
    held: set = set()
    entered: dict = {}
    baskets_positive, baskets_negative = [0], [0]
    cohort: dict = {}        # symbol -> rebalance index it was bought at
    entry_px: dict = {}      # symbol -> price paid, for return-since-entry
    prev_str: dict = {}
    prev_sel: set = set()
    sizes, turns = [], []

    for i in range(len(snaps) - 1):
        on = snaps[i]
        sel = alpha_selection(on, i, ind_at, px_at,
                              top_n=MAX_HOLD if mode == "ew30" else TOP_N)
        if not sel:
            eq.append(eq[-1])
            continue

        if mode in ("control", "ew30"):
            held = set(sel)
        elif mode == "net_positive_basket":
            # Book a NET GAIN on every exit batch. The basket of 5 must carry the 2
            # worst performers; the other 3 are the best available, so if any positive
            # combination exists this finds it. Falls back to a plain bottom-5 cut when
            # even the three best cannot offset the two worst.
            str_now = {x: strength(x, on) for x in set(sel) | held}
            if held:
                ret = {x: (px_at[x].iloc[i] / entry_px[x] - 1)
                       for x in held if entry_px.get(x)}
                if len(ret) >= 5:
                    by_ret = sorted(ret, key=lambda x: ret[x])
                    worst2, rest = by_ret[:2], by_ret[2:]
                    need = BASKET_TARGET * 5 - sum(ret[x] for x in worst2)
                    # Use the SMALLEST winners that still clear the bar, so the biggest
                    # compounders are not sold just to make the batch look green.
                    best = None
                    for combo in itertools.combinations(sorted(rest, key=lambda x: ret[x]), 3):
                        tot = sum(ret[x] for x in combo)
                        if tot >= need and (best is None or tot < best[0]):
                            best = (tot, combo)
                    if best:
                        held -= set(worst2) | set(best[1])
                        baskets_positive[0] += 1
                    else:
                        held -= set(by_ret[:5])       # cannot reach target -> plain cut
                        baskets_negative[0] += 1
            cand = [x for x in sel if x not in held and str_now.get(x) is not None]
            cand.sort(key=lambda x: str_now[x])       # replace with lowest-STR names
            want = SEED_N if not held else 5
            for a in cand[:want]:
                entry_px[a] = px_at[a].iloc[i]
                held.add(a)

        elif mode == "buy_low_sell_high":
            # The classic accumulate-cheap / release-extended rotation, on STR LEVEL.
            # Entry is the whole Alpha selection (not just new entrants): we want the
            # cheapest available name, regardless of when the strategy first wanted it.
            str_now = {x: strength(x, on) for x in set(sel) | held}
            if held and len(held) >= MAX_HOLD:
                hi = sorted([x for x in held if str_now.get(x) is not None],
                            key=lambda x: -str_now[x])          # MOST extended leaves
                held -= set(hi[:EXIT_N])
            cand = [x for x in sel if x not in held and str_now.get(x) is not None]
            cand.sort(key=lambda x: str_now[x])                 # LEAST extended enters
            room = MAX_HOLD - len(held)
            held |= set(cand[:min(SEED_N if not held else BUY_N, max(0, room))])

        elif mode.startswith("vintage"):
            # rank a cohort by realised return since entry, or by STR level
            def score(sym):
                if mode == "vintage_str":
                    return strength(sym, on) or -1e9
                p_in = entry_px.get(sym)
                return (px_at[sym].iloc[i] / p_in - 1) if p_in else 0.0

            # EXIT first, so freed slots are refilled the same cycle
            older = [x for x in held if cohort.get(x) == i - 2]
            recent = [x for x in held if cohort.get(x) == i - 1]
            older.sort(key=score, reverse=True)     # TOP of the matured cohort
            recent.sort(key=score)                  # BOTTOM of the recent cohort
            held -= set(older[:2]) | set(recent[:3])

            ranked = [x for x in sel if x not in held and strength(x, on) is not None]
            ranked.sort(key=lambda x: -strength(x, on))      # TOP by STR
            adds = ranked[:5]
            for a in adds:
                cohort[a] = i
                entry_px[a] = px_at[a].iloc[i]
            held |= set(adds)
        else:
            str_now = {s: strength(s, on) for s in set(sel) | held}
            # ENTRY: only names the strategy has JUST started wanting, least-run first.
            fresh = [s for s in sel if s not in prev_sel and s not in held
                     and str_now.get(s) is not None]
            if mode == "random_entry":
                rng.shuffle(fresh)          # same pool, no STR preference
            else:
                fresh.sort(key=lambda s: str_now[s])
            want = SEED_N if not held else BUY_N
            adds = fresh[:want]

            # EXIT: strength FADING -- the biggest drop in STR since last rebalance.
            # A name bought this cycle has no prior reading and cannot be judged yet.
            if held:
                if mode == "exit_oldest":
                    drops = sorted(held, key=lambda s: entered.get(s, 0))
                else:
                    deltas = {s: str_now[s] - prev_str[s] for s in held
                              if str_now.get(s) is not None and s in prev_str}
                    drops = sorted(deltas, key=lambda s: deltas[s])
                n_exit = EXIT_N if (len(held) + len(adds) > MAX_HOLD or len(held) >= MAX_HOLD) \
                    else (EXIT_N if len(held) >= MAX_HOLD else 0)
                # while ramping, only trim enough to respect the cap
                room = MAX_HOLD - len(held)
                if len(adds) > room:
                    n_exit = max(n_exit, len(adds) - room)
                held -= set(drops[:n_exit])
            held |= set(adds)
            for a in adds:
                entered.setdefault(a, i)
            prev_str = {s: str_now[s] for s in held if str_now.get(s) is not None}
            prev_sel = set(sel)

        names = [s for s in held if not pd.isna(px_at[s].iloc[i])
                 and not pd.isna(px_at[s].iloc[i + 1]) and px_at[s].iloc[i] > 0]
        if not names:
            eq.append(eq[-1])
            continue
        w = {s: 1.0 / len(names) for s in names}
        turnover = sum(abs(w.get(s, 0) - prev_w.get(s, 0))
                       for s in set(w) | set(prev_w)) / 2
        gross = sum(v * (px_at[s].iloc[i + 1] / px_at[s].iloc[i] - 1) for s, v in w.items())
        eq.append(eq[-1] * (1 + gross) * (1 - turnover * (BUY + SELL)))
        sizes.append(len(names))
        turns.append(turnover)
        prev_w = w
    if mode == "net_positive_basket":
        tot = baskets_positive[0] + baskets_negative[0]
        if tot:
            print(f"    [{which}] net-positive baskets formed "
                  f"{baskets_positive[0]}/{tot} = {baskets_positive[0]/tot:.0%}", flush=True)
    return snaps, eq, sizes, turns


def run_group_timing(which, min_group=3, rising_days=22):
    """Quarterly universe, MONTHLY in/out on whether a name's 1-month return is rising.

    The universe is the Alpha top-30, refreshed each quarter. Between refreshes the
    book is re-formed every month from whichever members have a positive 1-month
    return -- the "group". Fewer than `min_group` qualify and it sits in CASH.

    Searching subsets adds nothing: the best group of size m is just the top m by
    return, so the qualifying set IS the group.
    """
    g = pd.Series(cal.index).groupby([cal.index.year, cal.index.month])
    months = [d for d in sorted(pd.to_datetime(
        (g.min() if which == "first" else g.apply(lambda x: x.iloc[len(x) // 2]))).tolist())
        if d.date() >= SNAP_FROM]
    qsnaps = set(quarter_snaps(which))

    ind_at = {x: inds[x].reindex(months, method="ffill") for x in closes}
    px_at = {x: closes[x].reindex(months, method="ffill") for x in closes}

    eq, prev_w, universe = [1.0], {}, []
    sizes, turns, cash_months = [], [], 0
    for i in range(len(months) - 1):
        on = months[i]
        if on in qsnaps or not universe:
            universe = alpha_selection(on, i, ind_at, px_at, top_n=30)

        rets = {}
        for x in universe:
            h = closes[x][closes[x].index <= on]
            if len(h) > rising_days and h.iloc[-1 - rising_days] > 0                     and not pd.isna(px_at[x].iloc[i]) and not pd.isna(px_at[x].iloc[i + 1])                     and px_at[x].iloc[i] > 0:
                rets[x] = h.iloc[-1] / h.iloc[-1 - rising_days] - 1
        rising = [x for x, r in rets.items() if r > 0]

        # The group must reach into the BOTTOM HALF of the universe by 1-month return.
        # If only leaders are rising, strength is too narrow to trust -- that is the
        # breadth condition, and it is what makes the cash rule reachable at all.
        bottom_half = set(sorted(rets, key=lambda x: rets[x])[:max(1, len(rets) // 2)])
        has_laggard = any(x in bottom_half for x in rising)

        if len(rising) < min_group or not has_laggard:      # no valid group -> cash
            eq.append(eq[-1] * (1 - sum(prev_w.values()) / 2 * (BUY + SELL)))
            turns.append(sum(prev_w.values()) / 2)
            sizes.append(0)
            prev_w = {}
            cash_months += 1
            continue

        w = {x: 1.0 / len(rising) for x in rising}
        turnover = sum(abs(w.get(x, 0) - prev_w.get(x, 0)) for x in set(w) | set(prev_w)) / 2
        gross = sum(v * (px_at[x].iloc[i + 1] / px_at[x].iloc[i] - 1) for x, v in w.items())
        eq.append(eq[-1] * (1 + gross) * (1 - turnover * (BUY + SELL)))
        sizes.append(len(rising)); turns.append(turnover)
        prev_w = w
    print(f"    [{which}] in cash {cash_months}/{len(months)-1} months "
          f"({cash_months/(len(months)-1):.0%})", flush=True)
    return months, eq, sizes, turns


def perf(eq, snaps):
    a = np.asarray(eq, float)
    yrs = (snaps[len(a) - 1] - snaps[0]).days / 365.25
    cagr = a[-1] ** (1 / yrs) - 1
    r = np.diff(a) / a[:-1]
    sh = (r.mean() * 4) / (r.std(ddof=1) * 2) if r.std(ddof=1) > 0 else float("nan")
    return cagr, sh, (a / np.maximum.accumulate(a) - 1).min()


ARMS = [("group_timing", "GROUP-TIMING", "monthly rising group, must include a bottom-50% name, else cash"),
        ("net_positive_basket", "NET+BASKET", "exit 5 = 2 worst + smallest 3 clearing +3%"),
        ("str_rotate", "STR-ROTATE", "enter lowest STR (new entrants), exit fading STR"),
        ("ew30", "EW30", "plain Alpha top-30 -> the width finding"),
        ("control", "CONTROL", "plain Alpha top-20 (the live portfolio)")]
SEEDS = 5           # random arm is averaged over seeds so one lucky draw cannot win

print(f"{'arm':<14} {'CAGR':>7} {'Sharpe':>7} {'maxDD':>8} {'avg N':>7} {'turnover':>9}   note",
      flush=True)
for mode, label, note in ARMS:
    seeds = range(SEEDS) if mode == "random_entry" else [0]
    cs, ss, ds, ns, ts = [], [], [], [], []
    for sd in seeds:
        curves, sz, tn, sn = [], [], [], None
        for which in ("first", "mid"):
            snaps, eq, sizes, turns = (run_group_timing(which) if mode == "group_timing"
                                       else run_sleeve(which, mode, seed=sd))
            curves.append(np.asarray(eq, float))
            sz += sizes
            tn += turns
            sn = snaps
        n = min(len(c) for c in curves)
        blend = (curves[0][:n] + curves[1][:n]) / 2
        c, sh, d = perf(blend, sn)
        cs.append(c); ss.append(sh); ds.append(d); ns.append(np.mean(sz)); ts.append(np.mean(tn))
    spread = f"  (seed spread {max(cs)-min(cs):.2%})" if len(cs) > 1 else ""
    print(f"{label:<14} {np.mean(cs):>6.2%} {np.mean(ss):>7.2f} {np.mean(ds):>7.1%} "
          f"{np.mean(ns):>7.1f} {np.mean(ts):>8.1%}   {note}{spread}", flush=True)
