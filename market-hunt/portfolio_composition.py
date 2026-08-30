import numpy as np

EARLIEST_INCEPTION = "2022-01-01"


def validate_portfolio(p: dict, known_strategy_ids: set[str]) -> list[str]:
    """Return a list of validation error strings (empty == valid)."""
    errors: list[str] = []
    sleeves = p.get("sleeves") or []

    if not sleeves:
        errors.append("portfolio must have at least one sleeve")

    if sleeves:
        total_w = round(sum(float(s.get("weight", 0)) for s in sleeves), 4)
        if abs(total_w - 100) > 0.5:
            errors.append(f"sleeve weights must sum to 100 (got {total_w})")

    for s in sleeves:
        if s.get("strategy_id") not in known_strategy_ids:
            errors.append(f"unknown strategy_id: {s.get('strategy_id')}")
        if not s.get("universe"):
            errors.append("each sleeve requires a universe")
        w = float(s.get("weight", 0))
        if w <= 0 or w > 100:
            errors.append(f"sleeve weight must be between 0 and 100 (got {w})")

    inception = p.get("inception_date")
    if not inception or inception < EARLIEST_INCEPTION:
        errors.append(f"inception_date must be on or after {EARLIEST_INCEPTION}")

    return errors


def blend_curve(sleeve_results: list[list[dict]]) -> list[dict]:
    """Union dates across sleeves; sum portfolio_value, forward-filling each
    sleeve. holdings_count = DISTINCT symbols held across sleeves that day
    (a name held by two sleeves is one position — summing per-sleeve counts
    would double-count the overlap and read near-constant at the cap total)."""
    all_dates = sorted({d["date"] for sr in sleeve_results for d in sr})
    by_sleeve = [{d["date"]: (float(d["portfolio_value"]),
                              [h.get("symbol") for h in (d.get("holdings") or [])])
                  for d in sr} for sr in sleeve_results]
    last_v = [0.0] * len(sleeve_results)
    last_syms: list[list] = [[] for _ in sleeve_results]
    curve: list[dict] = []
    for dt in all_dates:
        total = 0.0
        names: set = set()
        for i, m in enumerate(by_sleeve):
            if dt in m:
                last_v[i], last_syms[i] = m[dt]
            total += last_v[i]
            names.update(last_syms[i])
        curve.append({"date": dt, "portfolio_value": round(total, 2),
                      "holdings_count": len(names)})
    return curve


def benchmark_curve(dates: list[str], benchmark_closes: dict[str, float],
                    total_capital: float) -> dict[str, float]:
    """₹ value of Nifty 50 bought-and-held with total_capital, indexed to first close."""
    base = None
    for dt in dates:
        if dt in benchmark_closes and benchmark_closes[dt]:
            base = float(benchmark_closes[dt])
            break
    out: dict[str, float] = {}
    if not base:
        return {dt: round(total_capital, 2) for dt in dates}
    last = total_capital
    for dt in dates:
        if dt in benchmark_closes and benchmark_closes[dt]:
            last = total_capital * float(benchmark_closes[dt]) / base
        out[dt] = round(last, 2)
    return out


def union_holdings(sleeve_books: list[tuple[str, list[dict]]],
                   blended_final: float) -> list[dict]:
    """Union latest holdings across sleeves; aggregate shared symbols; recompute weight/pnl%."""
    agg: dict[str, dict] = {}
    for label, holdings in sleeve_books:
        for h in holdings:
            sym = h["symbol"]
            a = agg.get(sym)
            if a is None:
                a = {"symbol": sym, "sleeves": [], "quantity": 0.0,
                     "market_value": 0.0, "cost": 0.0,
                     "current_price": h.get("current_price", 0)}
                agg[sym] = a
            mv = float(h.get("market_value", 0))
            a["quantity"] += float(h.get("quantity", 0))
            a["market_value"] += mv
            a["cost"] += mv - float(h.get("pnl", 0))
            if label not in a["sleeves"]:
                a["sleeves"].append(label)

    out: list[dict] = []
    for a in agg.values():
        mv, cost = a["market_value"], a["cost"]
        pnl = mv - cost
        out.append({
            "symbol": a["symbol"],
            "sleeve": ", ".join(a["sleeves"]),
            "quantity": round(a["quantity"], 4),
            "current_price": a["current_price"],
            "market_value": round(mv, 2),
            "weight": round(mv / blended_final * 100, 2) if blended_final else 0,
            "pnl": round(pnl, 2),
            "pnl_percent": round(pnl / cost * 100, 2) if cost else 0,
        })
    out.sort(key=lambda x: x["market_value"], reverse=True)
    return out


def build_rebalance_plan(current_book: list[dict], targets: list[dict],
                         prices: dict[str, float],
                         min_trade_value: float = 0.0,
                         symbol_meta: dict | None = None) -> dict:
    """Turn "what the strategy wants" into "what to key into the terminal".

    The probable-adds/exits lists say WHICH names move; they don't say how much,
    so they can't be executed from. This diffs the current book against the target
    book and returns a per-symbol order with quantity and value, plus the totals
    needed to check the cash works out.

    Symbols are aggregated across sleeves first — the operator trades ONE combined
    book, so a name both sleeves want is one order, not two.

    `min_trade_value` suppresses drift smaller than it to `hold`; equal-weight
    resizing otherwise emits an order for every surviving name every cycle.

    A target with no price is reported as `unpriced` and excluded from the totals —
    sizing it would mean inventing a fill quantity.
    """
    cur = {r["symbol"]: r for r in current_book}

    merged: dict[str, dict] = {}
    for t in targets:
        sym = t["symbol"]
        row = merged.setdefault(sym, {"target_value": 0.0, "sleeves": set()})
        row["target_value"] += float(t.get("target_value") or 0.0)
        if t.get("sleeve"):
            row["sleeves"].add(t["sleeve"])

    rows, unpriced = [], []
    buy_value = sell_value = 0.0
    for sym in sorted(set(merged) | set(cur)):
        tgt = merged.get(sym)
        held = cur.get(sym)
        current_value = float(held["market_value"]) if held else 0.0
        current_qty = float(held["quantity"]) if held else 0.0
        target_value = float(tgt["target_value"]) if tgt else 0.0
        price = float(prices.get(sym) or (held or {}).get("current_price") or 0.0)
        sleeve = ", ".join(sorted(tgt["sleeves"])) if tgt and tgt["sleeves"] else (
            (held or {}).get("sleeve") or "")

        base = {"symbol": sym, "sleeve": sleeve, "price": round(price, 2),
                "current_qty": round(current_qty, 4), "current_value": round(current_value, 2),
                "target_value": round(target_value, 2)}

        if target_value > 0 and price <= 0:
            unpriced.append(sym)
            rows.append({**base, "action": "unpriced", "target_qty": 0,
                         "delta_qty": 0, "delta_value": 0.0})
            continue

        delta_value = target_value - current_value
        # `<=` so an unchanged position (delta exactly 0) reads as hold under the
        # default threshold of 0 — sleeves that are not due this cycle rely on it.
        if abs(delta_value) <= min_trade_value:
            rows.append({**base, "action": "hold",
                         "target_qty": round(current_qty, 4),
                         "target_value": round(current_value, 2),
                         "delta_qty": 0, "delta_value": 0.0})
            continue

        target_qty = target_value / price if price > 0 else 0.0
        # Whole shares only — you cannot key a fractional order into a terminal.
        delta_qty = int(target_qty - current_qty) if target_value > 0 else -current_qty
        if target_value == 0:
            action = "sell"
        elif current_value == 0:
            action = "buy"
        else:
            action = "add" if delta_value > 0 else "trim"

        rows.append({**base, "action": action, "target_qty": round(target_qty, 4),
                     "delta_qty": round(delta_qty, 4), "delta_value": round(delta_value, 2)})
        if delta_value > 0:
            buy_value += delta_value
        else:
            sell_value += -delta_value

    order = {"buy": 0, "add": 1, "trim": 2, "sell": 3, "unpriced": 4, "hold": 5}
    rows.sort(key=lambda r: (order.get(r["action"], 9), -abs(r["delta_value"])))

    after = [r for r in rows if r["target_value"] > 0 or r["action"] == "hold"]
    book_value = sum(r["current_value"] for r in rows) or sum(r["target_value"] for r in rows)

    # The book you will actually own once the orders fill — the question the
    # adds/exits lists never answered.
    meta = symbol_meta or {}

    def _tier(sym: str) -> str:
        return (meta.get(sym) or {}).get("index_tier") or "Other"

    after_value = sum(r["target_value"] for r in after) or 1.0
    book_after = sorted(
        ({"symbol": r["symbol"], "sleeve": r["sleeve"],
          "tier": _tier(r["symbol"]),
          "sector": (meta.get(r["symbol"]) or {}).get("sector") or "Other",
          "price": r["price"],
          "quantity": round(r["target_qty"], 4),
          "value": r["target_value"],
          "weight": round(r["target_value"] / after_value * 100, 2),
          "status": "new" if r["current_value"] == 0 else "kept"}
         for r in after),
        key=lambda b: -b["value"])

    def _mix(pairs: list[tuple[str, float]]) -> list[dict]:
        total = sum(v for _, v in pairs) or 1.0
        agg: dict[str, dict] = {}
        for name, v in pairs:
            row = agg.setdefault(name, {"name": name, "value": 0.0, "count": 0})
            row["value"] += v
            row["count"] += 1
        out = [{**r, "value": round(r["value"], 2),
                "weight": round(r["value"] / total * 100, 2)} for r in agg.values()]
        return sorted(out, key=lambda r: -r["weight"])

    return {
        "rows": rows,
        "book_after": book_after,
        "mix_before": _mix([(_tier(r["symbol"]), r["current_value"])
                            for r in rows if r["current_value"] > 0]),
        "mix_after": _mix([(b["tier"], b["value"]) for b in book_after]),
        "summary": {
            "buy_value": round(buy_value, 2),
            "sell_value": round(sell_value, 2),
            "net_cash": round(sell_value - buy_value, 2),
            "positions_before": sum(1 for r in rows if r["current_value"] > 0),
            "positions_after": len(after),
            "new_positions": sum(1 for r in rows if r["action"] == "buy"),
            "exited_positions": sum(1 for r in rows if r["action"] == "sell"),
            # (buys + sells) / 2 as a share of the book — one name of two replaced == 50%.
            "turnover_percent": round((buy_value + sell_value) / 2 / book_value * 100, 2)
            if book_value else 0.0,
            "unpriced": sorted(unpriced),
        },
    }


def build_rebalance_history(sleeves: list[dict], sleeve_runs: list[dict]) -> list[dict]:
    """All rebalance events (adds/exits) across sleeves, chronological.
    Exit P&L comes from the engine's exited_details."""
    events: list[dict] = []
    for s, run in zip(sleeves, sleeve_runs):
        for day in run.get("results", []):
            added = day.get("new_added") or []
            exited = day.get("exited") or []
            if not added and not exited:
                continue
            events.append({
                "date": day["date"],
                "sleeve": s["label"],
                "added": list(added),
                "exited": list(exited),
                "exit_details": [
                    {"symbol": e.get("symbol"),
                     "pnl": round(float(e.get("pnl", 0) or 0), 2),
                     "pnl_percent": round(float(e.get("pnl_percent", 0) or 0), 2)}
                    for e in (day.get("exited_details") or [])],
                "holdings_count": len(day.get("holdings") or []),
                "portfolio_value": day.get("portfolio_value"),
            })
    events.sort(key=lambda e: e["date"])
    return events


def run_allocation_stats(sleeve_results: list[list[dict]], symbol_meta: dict,
                         key: str) -> dict[str, dict]:
    """Per group (index tier / sector): time-averaged weight over the whole run
    and the group's chained return since run start.

    avg_weight: mean over trading days of the group's share of book value.
    run_return: daily price returns of names held on consecutive days
    (per-share value = market_value / quantity), value-weighted within the
    group, chained. Measures how the group's *held names* performed while
    held — flows from rebalancing don't distort it."""
    # date -> {symbol: (mv, per_share)} aggregated across sleeves
    by_date: dict[str, dict] = {}
    for sr in sleeve_results:
        for day in sr:
            slot = by_date.setdefault(day["date"], {})
            for h in day.get("holdings") or []:
                mv = float(h.get("market_value") or 0)
                qty = float(h.get("quantity") or 0)
                if mv <= 0 or qty <= 0:
                    continue
                sym = h.get("symbol")
                prev_mv, per_share = slot.get(sym, (0.0, 0.0))
                slot[sym] = (prev_mv + mv, mv / qty if prev_mv == 0 else per_share)

    def group_of(sym):
        return (symbol_meta.get(sym) or {}).get(key) or "Other"

    dates = sorted(by_date)
    weight_sum: dict[str, float] = {}
    cum: dict[str, float] = {}
    pnl_sum: dict[str, float] = {}   # ₹ price-move P&L while held, per group
    for i, dt in enumerate(dates):
        slot = by_date[dt]
        total = sum(mv for mv, _ in slot.values())
        if total <= 0:
            continue
        gmv: dict[str, float] = {}
        for sym, (mv, _) in slot.items():
            gmv[group_of(sym)] = gmv.get(group_of(sym), 0.0) + mv
        for g, mv in gmv.items():
            weight_sum[g] = weight_sum.get(g, 0.0) + mv / total
        if i == 0:
            continue
        prev = by_date[dates[i - 1]]
        rets: dict[str, list] = {}
        for sym, (mv1, ps1) in slot.items():
            if sym not in prev:
                continue
            mv0, ps0 = prev[sym]
            if ps0 > 0 and ps1 > 0 and mv0 > 0:
                rets.setdefault(group_of(sym), []).append((mv0, ps1 / ps0 - 1.0))
        for g, pairs in rets.items():
            w = sum(mv for mv, _ in pairs)
            if w > 0:
                r = sum(mv * ret for mv, ret in pairs) / w
                cum[g] = cum.get(g, 1.0) * (1.0 + r)
                pnl_sum[g] = pnl_sum.get(g, 0.0) + sum(mv * ret for mv, ret in pairs)

    n_days = max(len(dates), 1)
    return {g: {"avg_weight": round(weight_sum.get(g, 0.0) / n_days * 100, 2),
                "run_return": round((cum.get(g, 1.0) - 1.0) * 100, 2),
                "run_pnl": round(pnl_sum.get(g, 0.0), 2)}
            for g in set(weight_sum) | set(cum) | set(pnl_sum)}


def allocation_breakdown(current_book: list[dict], symbol_meta: dict, key: str) -> list[dict]:
    """Group the current book by symbol_meta[symbol][key] (e.g. index tier or
    sector): weight %, name count, and value-weighted return till date."""
    total_mv = sum(float(b.get("market_value", 0)) for b in current_book)
    groups: dict[str, dict] = {}
    for b in current_book:
        name = (symbol_meta.get(b["symbol"]) or {}).get(key) or "Other"
        g = groups.setdefault(name, {"name": name, "market_value": 0.0,
                                     "cost": 0.0, "pnl": 0.0, "count": 0,
                                     "symbols": []})
        mv = float(b.get("market_value", 0))
        pnl = float(b.get("pnl", 0))
        g["market_value"] += mv
        g["cost"] += mv - pnl
        g["pnl"] += pnl
        g["count"] += 1
        g["symbols"].append(b["symbol"])
    out = []
    for g in groups.values():
        out.append({
            "name": g["name"],
            "weight": round(g["market_value"] / total_mv * 100, 2) if total_mv else 0,
            "market_value": round(g["market_value"], 2),
            "count": g["count"],
            "pnl": round(g["pnl"], 2),
            "return_till_date": round(g["pnl"] / g["cost"] * 100, 2) if g["cost"] else 0,
            "symbols": g["symbols"],
        })
    out.sort(key=lambda x: -x["weight"])
    return out


def max_drawdown(values: list[float]) -> float:
    """Worst peak-to-trough drawdown (%) from a series of values. Returns ≤ 0."""
    peak = float("-inf")
    mdd = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak * 100
            if dd < mdd:
                mdd = dd
    return round(mdd, 2)


def sharpe_ratio(values: list[float], rf: float = 0.06) -> float:
    """Annualized Sharpe ratio (252 trading days) on simple daily returns; 0 if <2 points or 0 vol."""
    if len(values) < 2:
        return 0.0
    arr = np.array(values, dtype=float)
    rets = np.diff(arr) / arr[:-1]
    rets = rets[np.isfinite(rets)]
    if rets.size == 0:
        return 0.0
    vol = float(np.std(rets)) * np.sqrt(252)
    ann = (1 + float(np.mean(rets))) ** 252 - 1
    return round((ann - rf) / vol, 3) if vol > 0 else 0.0


def summary_metrics(curve: list[dict], total_capital: float) -> dict:
    """Summary metrics from portfolio and benchmark curves."""
    pf = [c["portfolio_value"] for c in curve]
    bm = [c.get("benchmark_value", total_capital) for c in curve]
    final_pf = pf[-1] if pf else total_capital
    final_bm = bm[-1] if bm else total_capital
    total_return = (final_pf / total_capital - 1) * 100 if total_capital else 0.0
    bench_return = (final_bm / total_capital - 1) * 100 if total_capital else 0.0
    return {
        "total_value": round(final_pf, 2),
        "total_pnl": round(final_pf - total_capital, 2),
        "total_return": round(total_return, 2),
        "benchmark_return": round(bench_return, 2),
        "alpha": round(total_return - bench_return, 2),
        "max_drawdown": max_drawdown(pf),
        "sharpe_ratio": sharpe_ratio(pf),
    }


def compose_portfolio(*, sleeves: list[dict], sleeve_runs: list[dict],
                      total_capital: float, benchmark_closes: dict[str, float],
                      portfolio_meta: dict, as_of: str,
                      symbol_meta: dict | None = None) -> dict:
    """Orchestrate full portfolio composition from sleeve simulation outputs."""
    sleeve_results = [run.get("results", []) for run in sleeve_runs]

    curve = blend_curve(sleeve_results)
    dates = [c["date"] for c in curve]
    bm = benchmark_curve(dates, benchmark_closes, total_capital)
    for c in curve:
        c["benchmark_value"] = bm.get(c["date"], total_capital)

    blended_final = curve[-1]["portfolio_value"] if curve else total_capital

    sleeve_books = []
    for s, run in zip(sleeves, sleeve_runs):
        results = run.get("results", [])
        holdings = results[-1].get("holdings", []) if results else []
        sleeve_books.append((s["label"], holdings))
    current_book = union_holdings(sleeve_books, blended_final)

    summary = summary_metrics(curve, total_capital)

    attribution = []
    add, exit_ = [], []
    for s, run in zip(sleeves, sleeve_runs):
        results = run.get("results", [])
        sleeve_return = float(run.get("summary", {}).get("total_return", 0.0))
        last = results[-1] if results else {}
        attribution.append({
            "id": s["id"], "label": s["label"], "weight": s["weight"],
            "total_return": round(sleeve_return, 2),
            "contribution": round(sleeve_return * float(s["weight"]) / 100, 2),
            "holdings_count": len(last.get("holdings", [])),
            "final_holdings": sorted(h.get("symbol") for h in last.get("holdings", [])),
            "coverage": run.get("point_in_time_coverage"),
        })
        for sym in last.get("new_added", []):
            add.append({"symbol": sym, "sleeve": s["label"], "target_weight": None})
        for sym in last.get("exited", []):
            exit_.append({"symbol": sym, "sleeve": s["label"]})

    symbol_meta = symbol_meta or {}
    # Tag each book row with its index tier and sector for the terminal view.
    for b in current_book:
        meta = symbol_meta.get(b["symbol"]) or {}
        b["index_tier"] = meta.get("index_tier") or "Other"
        b["sector"] = meta.get("sector") or "Other"

    def _alloc(key: str) -> list[dict]:
        rows = allocation_breakdown(current_book, symbol_meta, key)
        stats = run_allocation_stats(sleeve_results, symbol_meta, key)
        by_name = {r["name"]: r for r in rows}
        for name, st in stats.items():
            row = by_name.get(name)
            if row is None:
                row = {"name": name, "weight": 0.0, "market_value": 0.0, "count": 0,
                       "pnl": 0.0, "return_till_date": 0.0, "symbols": []}
                rows.append(row)
            row.update(st)
        for r in rows:
            r.setdefault("avg_weight", 0.0)
            r.setdefault("run_return", 0.0)
            r.setdefault("run_pnl", 0.0)
            # contribution: percentage points of the portfolio's total return
            # attributable to this group's price moves while held. Sums across
            # groups ≈ gross price-move return (differs from net total by
            # charges and cash timing).
            r["contribution_pts"] = (round(r["run_pnl"] / total_capital * 100, 2)
                                     if total_capital else 0.0)
        rows.sort(key=lambda x: -x["contribution_pts"])
        return rows

    return {
        "portfolio": {"id": portfolio_meta.get("id"), "name": portfolio_meta.get("name"),
                      "as_of": as_of, "inception_date": dates[0] if dates else None,
                      "point_in_time": portfolio_meta.get("point_in_time", True)},
        "summary": summary,
        "curve": curve,
        "current_book": current_book,
        "sleeves": attribution,
        "rebalance_actions": {"next_due": None, "add": add, "exit": exit_},
        "allocation": {
            "index_tier": _alloc("index_tier"),
            "sector": _alloc("sector"),
        },
        "rebalance_history": build_rebalance_history(sleeves, sleeve_runs),
    }
