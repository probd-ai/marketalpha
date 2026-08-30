import math
import statistics
from datetime import datetime
from membership import members_as_of
from wave_signals import CrossState, count_firing_groups, any_group_fires, group_fires

# A short book is force-liquidated (margin call) when net equity falls to this
# fraction of the short exposure — long before equity reaches zero, mirroring a
# broker's maintenance-margin call. Liquidating here leaves a small positive cash
# residual and avoids the near-zero-equity return blow-up that poisons Sharpe.
SHORT_MAINTENANCE_MARGIN = 0.25

# NOTE: get_rebalance_dates and apply_strategy_rules are imported lazily inside
# run_wave_simulation to avoid FastAPI/MongoDB side-effects at unit-test import time.


def summarize(eq_curve, N0, bench, start, end, rf=0.06):
    """Compute summary statistics using the same formulas as the oracle scripts.

    Parameters
    ----------
    eq_curve : list of (date_str, equity_float)
    N0       : float  — initial capital
    bench    : dict   — {date_str: benchmark_value}
    start    : str    — "%Y-%m-%d" start date
    end      : str    — "%Y-%m-%d" end date
    rf       : float  — annualised risk-free rate (default 0.06)

    Returns dict with keys: total_return, cagr, sharpe_ratio, max_drawdown,
    benchmark_cagr, alpha.
    """
    eq = [e for _, e in eq_curve]
    yrs = (datetime.strptime(end, "%Y-%m-%d").date() -
           datetime.strptime(start, "%Y-%m-%d").date()).days / 365.25
    total_return = (eq[-1] / N0 - 1) * 100
    cagr = ((eq[-1] / N0) ** (1 / yrs) - 1) * 100 if eq[-1] > 0 and yrs > 0 else 0
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq)) if eq[i - 1] > 0]
    if len(rets) > 1:
        mu = statistics.mean(rets)
        sd = statistics.stdev(rets)
        ann_ret = (1 + mu) ** 252 - 1
        ann_vol = sd * math.sqrt(252)
        sharpe = (ann_ret - rf) / ann_vol if ann_vol > 0 else 0
    else:
        sharpe = 0
    peak = -1e18
    mdd = 0
    for e in eq:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    mdd *= 100
    bdates = [ds for ds, _ in eq_curve if ds in bench]
    bench_cagr = (
        ((bench[bdates[-1]] / bench[bdates[0]]) ** (1 / yrs) - 1) * 100
        if bdates else 0
    )
    return {
        "total_return": total_return,
        "cagr": cagr,
        "sharpe_ratio": sharpe,
        "max_drawdown": mdd,
        "benchmark_cagr": bench_cagr,
        "alpha": cagr - bench_cagr,
    }

def _exit_is_takeprofit(exit_groups, today, prev, last_dirs):
    """A firing exit group expresses take-profit intent if it contains a
    non-negated signal with upward direction; otherwise it's a stop."""
    for g in exit_groups:
        if not group_fires(g, today, prev, last_dirs):
            continue
        for sig in g:
            if not sig.negate and getattr(sig, "direction", None) == "up":
                return True
    return False

def _grids_used(wave):
    grids = set()
    for grp in wave.entry + wave.accumulate + wave.exit:
        for s in grp:
            if s.kind == "last_cross_dir":
                grids.add((s.metric, tuple(s.grid)))
    return list(grids)

def _rank_top(entrants, ds, prices, indicators, momentum_method, free):
    """Rank entrant symbols by 20-day price ROC (descending) and return top `free`.

    Only `momentum_method="20_day_return"` is supported in v1.  Any other value
    raises ValueError immediately — this is intentional: we fail loud rather than
    silently rank by a method we don't implement (data-integrity principle).

    Scoring: for each symbol, look up its price on `ds` and the price 19 trading
    days earlier in the raw `prices[s]` dict (sorted date keys).  ROC =
    (current / past - 1) * 100.  If price data is missing the symbol scores 0.
    Ties broken alphabetically.  The async `select_top_stocks_by_momentum` and
    the bisect-series `calculate_stock_momentum_fast` are NOT called here.
    """
    if free <= 0:
        return []
    if free >= len(entrants):
        return entrants

    if momentum_method != "20_day_return":
        raise ValueError(
            f"wave-mode max_holdings ranking supports only momentum_ranking='20_day_return' in v1; "
            f"got {momentum_method!r}. Either set momentum_ranking='20_day_return' or leave max_holdings unset."
        )

    def _score(s):
        sym_prices = prices.get(s, {})
        if not sym_prices:
            return 0.0
        current_px = sym_prices.get(ds)
        if current_px is None:
            return 0.0
        # 20-day return: use available history sorted by date
        sorted_dates = sorted(sym_prices.keys())
        idx = sorted_dates.index(ds) if ds in sorted_dates else -1
        if idx < 0:
            return 0.0
        lookback_idx = max(0, idx - 19)
        past_px = sym_prices.get(sorted_dates[lookback_idx], current_px)
        return ((current_px / past_px) - 1) * 100 if past_px > 0 else 0.0

    ranked = sorted(entrants, key=lambda s: (-_score(s), s))  # desc score, alpha tiebreak
    return ranked[:free]


def _passes_rules(indicator_values, rules):
    """Return True if indicator_values satisfies all rules (same logic as
    apply_strategy_rules but for a single symbol without a Mongo/FastAPI import)."""
    if not rules:
        return True
    for rule in rules:
        val = (indicator_values.get(rule["metric"]) or 0)
        threshold = rule["threshold"]
        op = rule["operator"]
        if op == ">" and not (val > threshold):
            return False
        elif op == "<" and not (val < threshold):
            return False
        elif op == ">=" and not (val >= threshold):
            return False
        elif op == "<=" and not (val <= threshold):
            return False
        elif op == "==" and not (val == threshold):
            return False
        elif op == "!=" and not (val != threshold):
            return False
    return True


def run_wave_simulation(ever, intervals, prices, indicators, bench, dates, wave, *,
                        rules=None, max_holdings=None, momentum_method="20_day_return",
                        include_brokerage=False, custom_brokerage_rate=0.0,
                        point_in_time=True, side="long", annual_borrow_rate=0.0):
    # side: "long" (default) | "short". A short run is a uniform sign-flip of the
    # whole book: entries SELL borrowed shares (cash up), exits COVER (cash down),
    # equity = cash + sign·Σ(shares·price), and the position profits when price falls.
    # The long path (sign=+1) is byte-for-byte unchanged. annual_borrow_rate is the
    # stock-loan financing cost (decimal/yr) accrued daily on short market value;
    # ignored when long. NOTE: no margin/dividend-owed modelling in v1 — flagged.
    # PIT guard — fail loud rather than silently misreport.
    if point_in_time and not intervals:
        raise ValueError(
            "universe lacks point-in-time reconstitution; cannot run PIT-honest"
        )

    # Lazy import to avoid FastAPI/MongoDB side-effects at unit-test import time.
    # get_rebalance_dates is a pure utility; apply_strategy_rules is also pure —
    # but importing api_server initialises FastAPI app & middleware at module level,
    # so we defer the import to here.  If the import is ever moved to module top
    # and causes issues in tests, revert to this lazy pattern.
    if wave.buy_cadence != "daily":
        from engine.stateless import get_rebalance_dates
        buy_dates = get_rebalance_dates(dates, wave.buy_cadence, wave.buy_date)
    else:
        buy_dates = set(dates)

    N0 = wave.capital_N; k = wave.tranches; tranche = N0 / k
    accum_groups = wave.accumulate if wave.accumulate else wave.entry

    # cash_box: single-element list so _buy_one closure can mutate cash.
    cash_box = [N0]
    pos = {}                      # symbol -> {shares, cost, open_date, tranches}
    cs = {s: CrossState(_grids_used(wave)) for s in ever}
    last_close = {}
    # Benchmark rebasing: like the stateless engine, the benchmark curve starts at
    # the same capital (N0) and tracks benchmark returns, so both lines are comparable
    # in ₹. bench_base = first available close in the window; last_bench_close carries
    # forward on gap days.
    bench_base = next((bench[d] for d in dates if d in bench and bench[d]), None)
    last_bench_close = bench_base
    ever_held = set()
    barred = set()                # symbols permanently barred when reentry=False
    ruined = False                # short book wiped out (liability ≥ capital → margin call)
    ruin_date = None
    eq_curve = []
    results = []                  # per-day results list for frontend rendering
    diag = {"n_buys": 0, "n_exits": 0, "exit_takeprofit": 0, "exit_stop": 0,
            "reentries": 0, "_names": [], "_cashpct": []}

    # wave_diagnostics accumulators
    _tranches_deployed = []       # int per day: sum of tranches across positions
    _pct_deployed = []            # float per day: % of capital deployed
    _wdiag = {"take_profits": 0, "stops": 0}

    brokerage_rate = custom_brokerage_rate if include_brokerage else 0.0
    sign = -1.0 if side == "short" else 1.0
    daily_borrow = (annual_borrow_rate / 365.0) if (sign < 0 and annual_borrow_rate) else 0.0

    def _buy_one(s, px, ds):
        """Open/add one tranche of symbol s at price px. Mutates cash_box, pos, diag.

        LONG (sign=+1): buy — spend min(tranche, cash); brokerage deducted from cash
        without changing shares acquired, so the gross curve (rate=0) is unchanged.

        SHORT (sign=-1): sell-to-open — short the same tranche notional (cash is NOT a
        constraint; opening generates proceeds), brokerage reduces the proceeds. Both
        record cost basis = notional so avg_price = px, and P&L is signed downstream.
        """
        if pos.get(s, {}).get("tranches", 0) >= k:
            return
        if sign > 0:
            if cash_box[0] < 1.0:
                return
            spend = min(tranche, cash_box[0])
            # Brokerage: deduct extra cost from cash without changing shares acquired.
            # gross path (rate=0) → spend_effective == spend, behaviour identical to Task 6.
            spend_effective = min(spend * (1 + brokerage_rate), cash_box[0])
            sh = spend / px  # shares always based on gross spend → gross curve unchanged
            cash_box[0] -= spend_effective
        else:
            spend = tranche                      # short notional basis → avg_price = px
            sh = spend / px
            cash_box[0] += spend * (1 - brokerage_rate)   # short-sale proceeds, net of fees
        if s in pos:
            pos[s]["shares"] += sh
            pos[s]["cost"] += spend
            pos[s]["tranches"] += 1
        else:
            pos[s] = {"shares": sh, "cost": spend, "open_date": ds, "tranches": 1}
            if s in ever_held:
                diag["reentries"] += 1
            ever_held.add(s)
        diag["n_buys"] += 1

    for ds in dates:
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        members = members_as_of(intervals, d)

        for s in ever:
            if s in prices and ds in prices[s]:
                last_close[s] = prices[s][ds]

        # snapshot prev + advance cross-state
        ctx = {}
        for s in ever:
            today = indicators[s].get(ds)
            if today is None:
                continue
            prev = cs[s].prev_snapshot()
            cs[s].update(today)
            ctx[s] = (today, prev, cs[s].last_dirs)

        # Track per-day entries/exits for results list
        day_added = []
        day_exited = []

        # EXIT (always daily). After ruin the book is closed — no trading.
        for s in (list(pos.keys()) if not ruined else []):
            if s not in ctx:
                continue
            today, prev, last_dirs = ctx[s]
            if any_group_fires(wave.exit, today, prev, last_dirs):
                px = prices[s].get(ds, last_close.get(s))
                if px:
                    if wave.exit_mode == "fractional" and pos[s]["tranches"] > 1:
                        # close exactly one tranche; position persists with (tranches-1).
                        # sign: long sells (cash up), short covers (cash down).
                        sell_sh = pos[s]["shares"] / pos[s]["tranches"]
                        cash_box[0] += sign * sell_sh * px
                        pos[s]["shares"] -= sell_sh
                        pos[s]["tranches"] -= 1
                        # fractional partial exits are not counted in n_exits
                    else:
                        # full close (either mode="full" or last fractional tranche)
                        cash_box[0] += sign * pos[s]["shares"] * px
                        tp = _exit_is_takeprofit(wave.exit, today, prev, last_dirs)
                        diag["exit_takeprofit"] += 1 if tp else 0
                        diag["exit_stop"] += 0 if tp else 1
                        diag["n_exits"] += 1
                        _wdiag["take_profits"] += 1 if tp else 0
                        _wdiag["stops"] += 0 if tp else 1
                        if not wave.reentry:
                            barred.add(s)
                        day_exited.append(s)
                        del pos[s]

        # BUY (cadence-gated). Suppressed once the short book has been wiped out.
        if ds in buy_dates and not ruined:
            prev_pos_keys = set(pos.keys())

            if max_holdings is None:
                # --- Unified pass over ever in sorted order (matches oracle semantics) ---
                # Accumulate already-held names and enter new names in a single pass,
                # preserving the alphabetical iteration order the oracle uses.  Cash is
                # shared between accumulation and new entries — whoever comes first
                # alphabetically wins when cash is tight.
                #
                # PIT membership gate:
                #   - For new entries: always gated by current members (PIT-correct).
                #   - For accumulation (daily cadence): NOT gated by members — the
                #     level-wave oracle accumulates held positions on signal regardless
                #     of current index membership (once held, keep accumulating).
                #   - For accumulation (non-daily cadence): IS gated by members —
                #     matching the bull-wave oracle which uses a single membership-gated
                #     loop over all symbols (held and new alike).
                for s in ever:
                    if s not in ctx:
                        continue
                    px = prices[s].get(ds, last_close.get(s))
                    if not px:
                        continue
                    today, prev, last_dirs = ctx[s]
                    if s in pos:
                        # Accumulate: use accum_groups (= entry when accumulate=[])
                        # Non-daily cadences (monthly/weekly/quarterly) gate accumulation
                        # by current PIT membership — matches the bull-wave oracle which
                        # uses a single `for s in ever: if s not in members: continue`
                        # loop covering both held and new symbols.  Daily cadence does NOT
                        # gate accumulation by members — once a signal fires intra-day the
                        # level-wave oracle does not re-check membership (levels are crossed
                        # regardless of whether the stock was removed from the index).
                        if wave.buy_cadence != "daily" and s not in members:
                            continue
                        fires = count_firing_groups(accum_groups, today, prev, last_dirs)
                        for _ in range(fires):
                            _buy_one(s, px, ds)
                    else:
                        # New entry: must be a PIT member, pass rules, not barred
                        if s not in members:
                            continue
                        if s in barred:
                            continue
                        if not _passes_rules(today, rules):
                            continue
                        fires = count_firing_groups(wave.entry, today, prev, last_dirs)
                        for _ in range(fires):
                            _buy_one(s, px, ds)
            else:
                # --- Two-pass logic when max_holdings is set ---
                # Accumulation into already-held names is never gated by max_holdings.
                for s in list(pos.keys()):
                    if s not in ctx:
                        continue
                    today, prev, last_dirs = ctx[s]
                    px = prices[s].get(ds, last_close.get(s))
                    if not px:
                        continue
                    fires = count_firing_groups(accum_groups, today, prev, last_dirs)
                    for _ in range(fires):
                        _buy_one(s, px, ds)

                # New entries: ranked and capped by max_holdings
                entrants = []
                for s in ever:
                    if s not in ctx:
                        continue
                    if s in pos:
                        continue            # accumulation handled above
                    if s not in members:
                        continue            # not in universe on this date
                    if s in barred:
                        continue            # reentry=False and already exited
                    today, prev, last_dirs = ctx[s]
                    if not _passes_rules(today, rules):
                        continue            # pre-gate
                    fires = count_firing_groups(wave.entry, today, prev, last_dirs)
                    if fires > 0:
                        entrants.append((s, fires))

                # max_holdings: limit to free slots
                free = max(0, max_holdings - len(pos))
                if len(entrants) > free:
                    syms = _rank_top([s for s, _ in entrants], ds, prices, indicators,
                                     momentum_method, free)
                    sym_set = set(syms)
                    entrants = [(s, f) for s, f in entrants if s in sym_set]

                for s, fires in entrants:
                    px = prices[s].get(ds, last_close.get(s))
                    if not px:
                        continue
                    for _ in range(fires):
                        _buy_one(s, px, ds)

            # Track new additions (symbols newly entered this day)
            day_added = [s for s in pos if s not in prev_pos_keys]

        # Gross market value of the book (always positive). For a short run this is
        # the buy-back liability; for a long run it's the holdings' worth.
        gross_mv = sum(pos[s]["shares"] * (prices[s].get(ds) or last_close.get(s, 0)) for s in pos)
        # Daily stock-loan financing cost on the short book (no-op when long).
        if daily_borrow:
            cash_box[0] -= daily_borrow * gross_mv

        cash = cash_box[0]
        equity = cash + sign * gross_mv          # sign=+1 long, -1 short

        # Margin-call liquidation (shorts only): a real short book is force-closed
        # when net equity falls to the maintenance margin of the short exposure —
        # well before equity reaches zero. Cover the whole book at today's prices,
        # keep the small positive residual in cash, and halt. Models reality and
        # avoids the near-zero-equity return blow-up that poisons Sharpe/CAGR.
        if (not ruined and sign < 0 and gross_mv > 0
                and equity <= SHORT_MAINTENANCE_MARGIN * gross_mv):
            ruined = True
            ruin_date = ds
            cash_box[0] = max(equity, 0.0)       # residual after covering everything
            pos.clear()
            cash = cash_box[0]
            gross_mv = 0.0
            equity = cash
        eq_curve.append((ds, equity))
        diag["_names"].append(len(pos))
        diag["_cashpct"].append(100.0 * cash / equity if equity > 0 else 0)

        # wave_diagnostics series
        td = sum(p["tranches"] for p in pos.values())
        _tranches_deployed.append(td)
        # % of the CURRENT book that is in the market (0..100). Denominator is
        # current equity, not initial capital — otherwise portfolio growth pushes
        # "deployed" past 100%. Use gross market value so the metric stays positive
        # for shorts (equity - cash is negative when short).
        _pct_deployed.append(100.0 * gross_mv / equity if equity > 0 else 0.0)

        # Per-day benchmark: raw close (carried on gap days) for benchmark_price,
        # and a capital-rebased value (starts at N0) for the comparable curve.
        if ds in bench and bench[ds]:
            last_bench_close = bench[ds]
        bench_close = last_bench_close or 0
        bench_value = (N0 * bench_close / bench_base) if bench_base else N0

        # Build holdings snapshot mirroring the stateless engine's holding_info shape
        holdings_snap = []
        for s in pos:
            p = pos[s]
            shares = p["shares"]
            cost = p["cost"]
            avg_price = cost / shares if shares > 0 else 0.0
            current_price = prices[s].get(ds) or last_close.get(s, 0.0)
            market_value = shares * current_price
            # P&L sign-flips for shorts: profit when price falls below avg_price.
            pnl_h = sign * (current_price - avg_price) * shares
            pnl_pct = sign * ((current_price / avg_price) - 1) * 100 if avg_price > 0 else 0.0
            weight = market_value / equity * 100 if equity > 0 else 0.0
            holdings_snap.append({
                "symbol": s,
                "company_name": s,
                "quantity": shares,
                "avg_price": avg_price,
                "current_price": current_price,
                "market_value": market_value,
                "pnl": pnl_h,
                "pnl_percent": pnl_pct,
                "sector": "Unknown",
                "weight": weight,
                "side": side,
            })

        # Build exited_details for today's exits
        day_exited_details = []
        for s in day_exited:
            # At exit time the position was already removed from pos; use prices dict
            exit_price = prices[s].get(ds) or last_close.get(s, 0.0)
            # We no longer have cost/shares after del pos[s]; track via last_close heuristic:
            # avg_price is unknown post-exit so we emit 0 for it (position is gone).
            # day_exited is populated only on full exits (fractional partial exits are skipped).
            day_exited_details.append({
                "symbol": s,
                "company_name": s,
                "quantity": 0.0,   # shares already removed from pos
                "avg_price": 0.0,
                "exit_price": exit_price,
                "pnl": 0.0,
                "pnl_percent": 0.0,
                "sector": "Unknown",
            })

        prev_equity = eq_curve[-2][1] if len(eq_curve) >= 2 else N0
        day_pnl = equity - prev_equity if len(eq_curve) >= 2 else 0.0

        results.append({
            "date": ds,
            "portfolio_value": equity,
            "benchmark_value": bench_value,
            "holdings": holdings_snap,
            "new_added": day_added,
            "exited": day_exited,
            "exited_details": day_exited_details,
            "cash": cash,
            "total_pnl": equity - N0,
            "day_pnl": day_pnl,
            "benchmark_price": bench_close,
        })

    diag["avg_names"] = statistics.mean(diag["_names"]) if diag["_names"] else 0
    diag["avg_cash_pct"] = statistics.mean(diag["_cashpct"]) if diag["_cashpct"] else 0
    diag["ruined"] = ruined
    diag["ruin_date"] = ruin_date

    wave_diagnostics = {
        "tranches_deployed": _tranches_deployed,
        "pct_deployed_series": _pct_deployed,
        "take_profits": _wdiag["take_profits"],
        "stops": _wdiag["stops"],
    }

    start = dates[0] if dates else ""
    end = dates[-1] if dates else ""
    summary = summarize(eq_curve, N0, bench, start, end) if dates else {}

    # benchmark_return: percent return of benchmark over the run
    bdates_present = [ds for ds in dates if ds in bench]
    if len(bdates_present) >= 2:
        bench_return = ((bench[bdates_present[-1]] / bench[bdates_present[0]]) - 1) * 100
    else:
        bench_return = 0.0

    # total_trades: sum of all buys + full exits across the run
    total_trades = diag["n_buys"] + diag["n_exits"]

    summary["benchmark_return"] = bench_return
    summary["total_trades"] = total_trades
    summary["ruined"] = ruined
    summary["ruin_date"] = ruin_date

    return {
        "equity_curve": eq_curve,
        "cash": cash_box[0],
        "positions": pos,
        "diagnostics": diag,
        "summary": summary,
        "results": results,
        "wave_diagnostics": wave_diagnostics,
    }
