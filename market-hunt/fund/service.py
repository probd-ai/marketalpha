"""Trading-desk orchestration (spec §5–§8): compute a DRAFT run, publish it, record executions, report books.

compute_run
  1. load each sleeve's market from the database (fund.data);
  2. build each sleeve's rebalance schedule: back-filled weekdays before the first published run, then the fill date
     of every published run, then the next session F (this run);
  3. replay each sleeve in segments ending at every published run's signal date — comparing the state with the
     stored checkpoint (drift guard) — and on to the latest signal day S;
  4. project session F (prices = S's close) and step every sleeve once more: those are this week's model orders;
  5. blend the sleeves at target weights with the monthly reset on the first run day of each month;
  6. scale model orders to the fund's rupees, net and round them into the ticket; run the pre-trade checks.
"""
from __future__ import annotations

import copy
import hashlib
import threading
import json
import math
import uuid
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from . import checks as C
from .config import MIN_TRIM_DEFAULT, validate
from .data import BENCH, build_asset_market, build_market, charge_rates, market_holidays
from .engine import ENGINE_VERSION
from .engine.calendar import trading_days
from .engine.costs import Costs
from .engine.factory import base_schedule, make_book
from .engine.fund import blend
from .engine.projection import next_session, project
from .engine.replay import replay_sleeve
from .engine.types import Order, Reason, SleeveState
from .flows import resize_factors, total_units, validate_flow
from .ticket import build_ticket, held_before_ticket, orders_since


class RunRefused(Exception):
    """A business rule prevents the action (shown to the manager verbatim)."""


def _book(sl: dict):
    return make_book(sl["kind"], sl.get("params"), sl.get("symbol"))


def _market(db, sl: dict, model_start: date, today: date):
    if sl["kind"] == "asset":
        return build_asset_market(db, sl["symbol"], model_start)
    return build_market(db, sl["universe"], sl["kind"], model_start, today)


def _states_equal(a: SleeveState, b: dict, tol: float = 1e-6) -> list[str]:
    b = SleeveState.from_dict(b)
    diffs = []
    for s in sorted(set(a.lots) | set(b.lots)):
        qa, qb = a.qty(s), b.qty(s)
        if abs(qa - qb) > tol * max(abs(qa), abs(qb), 1e-12):
            diffs.append(s)
    if abs(a.cash - b.cash) > tol * max(abs(a.cash), abs(b.cash), 1.0):
        diffs.append("cash")
    return diffs


def _fingerprint(markets: dict, s: pd.Timestamp) -> str:
    h = hashlib.sha1()
    for k in sorted(markets):
        c = markets[k].closes.loc[:s]
        h.update(f"{k}|{c.shape}|{round(float(c.tail(30).sum().sum()), 4)}".encode())
    return h.hexdigest()[:16]


def _perf(curve: pd.Series) -> dict:
    c = curve.dropna()
    yrs = max((c.index[-1] - c.index[0]).days / 365.25, 1e-9)

    def back(days):
        a = c.index[c.index >= c.index[-1] - pd.Timedelta(days=days)][0]
        return float(c.iloc[-1] / c.loc[a] - 1) * 100

    return {"cagr": float((c.iloc[-1] / c.iloc[0]) ** (1 / yrs) - 1) * 100 if yrs >= 1 else None,
            "total_return": float(c.iloc[-1] / c.iloc[0] - 1) * 100,
            "max_dd": float((c / c.cummax() - 1).min()) * 100,
            "dd_now": float(c.iloc[-1] / c.max() - 1) * 100,
            "r_1m": back(30), "r_3m": back(91), "r_1y": back(365)}


def latest_bench_date(db) -> date | None:
    best = None
    for c in [c for c in db.list_collection_names() if c.startswith("prices_")]:
        d = db[c].find_one({"symbol": BENCH}, {"date": 1}, sort=[("date", -1)])
        if d and (best is None or d["date"] > best):
            best = d["date"]
    return best.date() if best else None


def _plan(cfg: dict, published: list[dict], bench: pd.Series, holidays: tuple) -> dict:
    """The model's calendar, shared by compute_run and performance so both replay exactly the same rebalances:
    signal day S (latest bench close), fill session F, each sleeve's schedule (back-filled weekdays before the first
    published run, then every published fill, then F) and the fund's reset / drift-band days."""
    ms = date.fromisoformat(cfg["model_start"])
    pub_fill = sorted(pd.Timestamp(r["fill_date"]) for r in published)
    first_pub = pub_fill[0] if pub_fill else None
    S = bench.dropna().index[-1]
    F = next_session(S, holidays)              # a holiday is never the fill session (Gandhi Jayanti 2026-10-02)
    week = list(F.isocalendar()[:2])
    cal = trading_days(bench, ms, S)
    in_f_week = lambda d: list(d.isocalendar()[:2]) == week
    k = int(cfg.get("backfill_weekday", 0))

    def schedule(kind):
        if kind == "asset":
            return set()
        base = base_schedule(kind, cal, k)
        back = {d for d in base if (first_pub is None or d < first_pub) and not in_f_week(d)}
        return back | set(pub_fill) | {F}

    scheds = {sl["id"]: schedule(sl["kind"]) for sl in cfg["sleeves"]}
    run_days = sorted(scheds[next(s["id"] for s in cfg["sleeves"] if s["kind"] != "asset")])
    # a config saved before this setting existed keeps its original rule: every month, no band
    months = set(cfg.get("rebalance_months", list(range(1, 13))))
    band = cfg.get("rebalance_band", None) if "rebalance_months" in cfg else None
    first_run_days, reset_days, seen = set(), set(), set()
    for d in run_days:
        if (d.year, d.month) not in seen:
            seen.add((d.year, d.month))
            first_run_days.add(d)
            if d.month in months:
                reset_days.add(d)
    return {"S": S, "F": F, "week": week, "scheds": scheds, "months": months, "band": band,
            "first_run_days": first_run_days, "reset_days": reset_days}


def compute_run(db, store, today: date | None = None, progress=lambda msg: None) -> dict:
    today = today or date.today()
    cfg = store.get_config()
    errs = validate(cfg, today)
    if errs:
        raise RunRefused("; ".join(errs))
    ms = date.fromisoformat(cfg["model_start"])
    cap = float(cfg["capital"])
    costs = Costs(*charge_rates(), float(cfg["slippage_bp"]) / 1e4)
    published = store.published_runs(cfg["version"])

    markets = {}
    for sl in cfg["sleeves"]:
        progress(f"loading {sl['label']}")
        markets[sl["id"]] = _market(db, sl, ms, today)
    bench = next(iter(markets.values())).closes[BENCH]
    holidays = tuple(sorted(market_holidays(db)))
    pl = _plan(cfg, published, bench, holidays)
    S, F, week = pl["S"], pl["F"], pl["week"]
    if any(list(pd.Timestamp(r["fill_date"]).isocalendar()[:2]) == week for r in published):
        raise RunRefused(f"a run is already published for ISO week {week[0]}-W{week[1]:02d}")
    scheds, months, band = pl["scheds"], pl["months"], pl["band"]
    first_run_days, reset_days = pl["first_run_days"], pl["reset_days"]

    # replay each sleeve through the published checkpoints to S, then one projected step to F
    cps = [pd.Timestamp(r["signal_date"]) for r in published]
    curves, st_S, st_F, orders_F, missed, mismatches = {}, {}, {}, {}, {}, []
    last_fill = pd.Timestamp(published[-1]["fill_date"]) if published else None
    for sl in cfg["sleeves"]:
        sid, m, book = sl["id"], markets[sl["id"]], _book(sl)
        progress(f"replaying {sl['label']}")
        pieces, resume = [], None
        for r, c in zip(published, cps):
            res = replay_sleeve(book, m, costs, ms, c, scheds[sid], 1.0, resume=resume)
            pieces.append(res.equity)
            resume = (c, copy.deepcopy(res.state))
            saved = (r.get("checkpoint") or {}).get(sid)
            if saved is not None:
                diff = _states_equal(res.state, saved)
                if diff:
                    mismatches.append({"run_id": r["_id"], "signal_date": str(c.date()), "sleeve": sl["label"],
                                       "symbols": diff[:10]})
        res = replay_sleeve(book, m, costs, ms, S, scheds[sid], 1.0, resume=resume)
        pieces.append(res.equity)
        curves[sid] = pd.concat(pieces)
        st_S[sid] = copy.deepcopy(res.state)
        missed[sid] = orders_since(res.orders, last_fill)
        fut = replay_sleeve(book, project(m, S, F), costs, ms, F, scheds[sid], 1.0, resume=(S, res.state))
        st_F[sid], orders_F[sid] = fut.state, fut.orders.get(F, [])

    weights = {sl["id"]: float(sl["weight"]) for sl in cfg["sleeves"]}
    idx = curves[next(iter(curves))].index
    px = pd.DataFrame({k2: v.reindex(idx).ffill() for k2, v in curves.items()})
    fr = blend(px, weights, float(cfg["reset_cost"]), reset_days=reset_days, band_days=first_run_days, band=band)
    # Capital is the money the fund has at go-live: anchor rupees so the fund equals `capital` on the first published
    # run's signal day (or on S before anything is published); after that it grows with the model.
    # Deposits / withdrawals after go-live buy or redeem units at the NAV of the run that applies them (fund.flows).
    t0 = pd.Timestamp(published[0]["signal_date"]) if published else S
    nav_s = float(fr.equity.loc[S])
    flows = store.cashflows(cfg["version"]) if published else []
    applied = [f for f in flows if f["status"] == "APPLIED"]
    pending = [f for f in flows if f["status"] == "PENDING"]
    units = total_units(cap, float(fr.equity.loc[t0]), [f["units"] for f in applied])
    flow = float(sum(f["amount"] for f in pending))
    rupees = {sid: units * float(fr.sleeve_values.loc[S, sid]) for sid in weights}
    total = sum(rupees.values())
    scale = {sid: rupees[sid] / float(curves[sid].loc[S]) for sid in weights}
    drift_now = max(abs(float(fr.sleeve_values.loc[S, k]) / float(fr.sleeve_values.loc[S].sum()) - weights[k])
                    for k in weights)
    scheduled = F in reset_days
    drifted = F in first_run_days and band is not None and drift_now > band
    is_reset = scheduled or drifted
    rebalance_why = (f"scheduled ({F.strftime('%b')})" if scheduled else
                     f"drift {drift_now * 100:.1f} pts > {band * 100:.0f}-pt band" if drifted else None)
    flow_error = None
    try:
        resize = resize_factors(rupees, weights, flow, is_reset)
    except ValueError as e:
        flow_error, resize = str(e), resize_factors(rupees, weights, 0.0, is_reset)
    resize_reasons = ([Reason.MONTHLY_RESET] if is_reset else []) + \
        ([Reason.DEPOSIT] if flow > 0 else [Reason.WITHDRAWAL] if flow < 0 else [])
    ref = {}
    for sid, m in markets.items():
        row = m.cclose.loc[S]
        ref.update({s: float(v) for s, v in row.items() if not pd.isna(v)})

    # ticket — the first run of a model version builds the whole book (the manager holds none of it yet); later
    # runs trade only this week's changes
    initial = not published
    tsl = {}
    for sl in cfg["sleeves"]:
        sid, m = sl["id"], markets[sl["id"]]
        ctx = {}
        for s in set(st_S[sid].lots) | {o.symbol for o in orders_F[sid]}:
            if s in m.roc.columns and not pd.isna(m.roc.at[S, s]):
                ctx[s] = {"roc22": float(m.roc.at[S, s]) * 100}
        after = {s: st_F[sid].qty(s) for s in st_F[sid].lots}
        if initial:
            f = weights[sid] * total / rupees[sid]            # build straight to the target weight
            build = [Order(s, "BUY", q * f, ref[s], q * f * ref[s], Reason.INITIAL_BUILD) for s, q in after.items()]
            tsl[sl["label"]] = {"orders": build, "scale": scale[sid], "held_before": {}, "held_after": after,
                                "reset_factor": None, "ctx": ctx}
            continue
        # trades the model made between the last published fill and S (index exits happen on any session) were never
        # on a ticket — carry them onto this one, dated, as full exits of what the manager still holds
        for d, o in missed[sid]:
            ctx.setdefault(o.symbol, {})["exit_date"] = d.strftime("%d %b")
        tsl[sl["label"]] = {"orders": [o for _, o in missed[sid]] + orders_F[sid], "scale": scale[sid],
                            "held_before": held_before_ticket({s: st_S[sid].qty(s) for s in st_S[sid].lots},
                                                              missed[sid]),
                            "held_after": after,
                            "reset_factor": resize[sid] if resize else None, "resize_reasons": resize_reasons,
                            "ctx": ctx}
    # cap trims worth less than min_trim_value are held back and carried to the next ticket until they add up
    carried = {} if initial else {(r["sleeve"], r["symbol"]): float(r["model_shares"])
                                  for r in (store.get_run(published[-1]["_id"]) or {}).get("residuals", [])
                                  if r.get("deferred_trim")}
    lines, residual = build_ticket(tsl, ref, float(cfg["limit_band"]),
                                   min_trim=float(cfg.get("min_trim_value", MIN_TRIM_DEFAULT)), carried=carried)
    tick = [l.to_dict() for l in lines]

    # checks
    ck = []
    ind_days = [m.indicator_last.max() for m in markets.values() if m.indicator_last is not None]
    ck.append(C.freshness(S.date(), today, min(ind_days).date() if ind_days else None, holidays))
    ck.append(C.calendar(F.date(), {h.year for h in holidays}))
    for sl in cfg["sleeves"]:
        sid, m = sl["id"], markets[sl["id"]]
        if sl["kind"] == "asset":
            ck.append(C.instrument(sl["label"], sl["symbol"], not pd.isna(m.closes.at[S, sl["symbol"]])
                                   if S in m.closes.index else False))
            continue
        mem = sorted(s for s in m.members(S.date()) if s in m.closes.columns)
        miss_px = [s for s in mem if pd.isna(m.closes.at[S, s])]
        miss_ind = [s for s in mem if m.indicator_last is None or pd.isna(m.indicator_last.get(s))
                    or m.indicator_last.get(s) < S]
        ck.append(C.coverage(sl["label"], mem, miss_px, miss_ind))
        val_f = st_F[sid].value({s: ref[s] for s in st_F[sid].lots})
        w = {s: st_F[sid].qty(s) * ref[s] / val_f for s in st_F[sid].lots} if val_f > 0 else {}
        if sl["kind"] == "tranche":
            ck.append(C.caps(sl["label"], w, float((sl.get("params") or {}).get("max_w", 0.10))))
        else:
            ck.append(C.concentration(sl["label"], w))
        ck.append(C.cash(sl["label"], st_F[sid].cash / val_f if val_f > 0 else 0.0))
    ack = (cfg.get("drift_ack") or {}).get("hash")
    mm_hash = hashlib.sha1(json.dumps(mismatches, sort_keys=True).encode()).hexdigest()[:12] if mismatches else None
    dck = C.drift(mismatches)
    if mismatches and ack == mm_hash:
        dck = C.Check("Drift", C.WARN, "data changed under a published week — acknowledged by the manager", mismatches)
    ck.append(dck)
    adv = {}
    for m in markets.values():
        if m.traded_value is not None:
            tv = m.traded_value.loc[:S].tail(20).mean()
            adv.update({s: float(v) for s, v in tv.items() if not pd.isna(v)})
    ck.append(C.liquidity(tick, adv))
    if pending or flow_error:
        verb = "buys" if flow > 0 else "sells"
        kind = "Deposit" if flow > 0 else "Withdrawal"
        ck.append(C.Check("Cash flow", C.BLOCK if flow_error else C.PASS,
                          flow_error or f"{kind} of ₹{abs(flow):,.0f} applied by this run — the ticket {verb} it "
                                        f"across the sleeves at target weights",
                          [{"id": f["_id"], "amount": f["amount"]} for f in pending]))

    # books at S (fund rupees)
    books = []
    for sl in cfg["sleeves"]:
        sid, m, st = sl["id"], markets[sl["id"]], st_S[sl["id"]]
        rows = []
        for s in st.lots:
            lots = st.lots[s]
            sh = st.qty(s) * scale[sid]
            v = sh * ref[s]
            row = {"symbol": s, "shares": sh, "price": ref[s], "value": v, "weight": v / rupees[sid] * 100,
                   "lots": len(lots), "oldest_age": max(l.age for l in lots),
                   "held_since": min(l.opened for l in lots).isoformat()}
            if s in m.roc.columns and not pd.isna(m.roc.at[S, s]):
                row["roc22"] = float(m.roc.at[S, s]) * 100
            if m.grid is not None and s in m.grid.columns and not pd.isna(m.grid.at[S, s]):
                row["grid"] = int(m.grid.at[S, s])
            if m.elig is not None and s in m.elig.columns:
                row["alpha"] = bool(m.elig.at[S, s])
            rows.append(row)
        rows.sort(key=lambda r: -r["value"])
        books.append({"id": sid, "label": sl["label"], "kind": sl["kind"], "target_weight": weights[sid] * 100,
                      "weight": rupees[sid] / total * 100, "value": rupees[sid], "cash": st.cash * scale[sid],
                      "holdings": rows, "curve_cagr": _perf(curves[sid])["cagr"]})

    fund_curve = cap * fr.equity
    nifty = bench.reindex(idx).ffill()
    snap = {
        "_id": f"R{S.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}", "version": cfg["version"],
        "engine_version": ENGINE_VERSION, "status": "DRAFT", "run_type": "weekly",
        "signal_date": str(S.date()), "fill_date": str(F.date()), "iso_week": f"{week[0]}-W{week[1]:02d}",
        "monthly_reset": is_reset and not initial,
        "rebalance": {"due": bool(is_reset and not initial), "why": rebalance_why, "drift_pts": drift_now * 100,
                      "months": sorted(months), "band_pts": band * 100 if band is not None else None}, "initial_build": initial, "fingerprint": _fingerprint(markets, S), "drift_hash": mm_hash,
        "verdict": C.verdict(ck), "checks": [c.to_dict() for c in ck], "ticket": tick, "residuals": residual,
        "nav_signal": nav_s, "units": units,
        "cash_flows": [{"id": f["_id"], "amount": f["amount"], "note": f.get("note", "")} for f in pending],
        "fund": {"capital": cap, "value": total + flow, "value_before_flows": total,
                 "net_invested": cap + sum(f["amount"] for f in applied) + flow,
                 "live_since": str(t0.date()) if published else None,
                 **_perf(fund_curve), "nifty": _perf(nifty), "model_start": cfg["model_start"]},
        "sleeves": [{"id": b["id"], "label": b["label"], "target_weight": b["target_weight"], "weight": b["weight"],
                     "value": b["value"], "cagr": b["curve_cagr"]} for b in books],
        "books": books,
        "curve": {"dates": [str(d.date()) for d in idx], "fund": [round(float(x), 2) for x in fund_curve],
                  "nifty": [round(float(x), 2) for x in cap * nifty / nifty.iloc[0]]},
        "checkpoint": {sid: st_S[sid].to_dict() for sid in st_S},
        "orders_model": sum(len(v) for v in orders_F.values()),
    }
    store.insert_run(snap)
    return snap


_write_lock = threading.Lock()     # serialises publish / execution writes in this process (no double publish)


def publish(db, store, run_id: str) -> dict:
    with _write_lock:
        return _publish(db, store, run_id)


def _publish(db, store, run_id: str) -> dict:
    run = store.get_run(run_id)
    if not run:
        raise RunRefused("run not found")
    if run["status"] != "DRAFT":
        raise RunRefused(f"run is {run['status']}, only a DRAFT can be published")
    cfg = store.get_config()
    if run["version"] != cfg["version"]:
        raise RunRefused("the fund settings changed after this run was computed — compute a new run")
    if run["verdict"] == C.BLOCK:
        blocked = [c["name"] for c in run["checks"] if c["status"] == C.BLOCK]
        raise RunRefused(f"blocked by pre-trade checks: {', '.join(blocked)}")
    latest = latest_bench_date(db)
    if latest and str(latest) > run["signal_date"]:
        raise RunRefused(f"newer prices exist ({latest}) than this run's signal day ({run['signal_date']}) — "
                         "compute a fresh run")
    if any(r["iso_week"] == run["iso_week"] for r in store.published_runs(cfg["version"])):
        raise RunRefused(f"a run is already published for {run['iso_week']}")
    snap_ids = {f["id"] for f in run.get("cash_flows", [])}
    now_ids = {f["_id"] for f in store.cashflows(cfg["version"]) if f["status"] == "PENDING"}
    if snap_ids != now_ids:
        raise RunRefused("cash flows changed after this run was computed — compute a new run so the ticket includes them")
    if not store.mark_published(run_id, cfg["version"], run["iso_week"],
                                datetime.now(timezone.utc).isoformat(timespec="seconds")):
        raise RunRefused(f"could not publish: the run changed state or {run['iso_week']} is already published")
    if snap_ids:
        store.apply_cashflows(sorted(snap_ids), run_id, float(run["nav_signal"]))
    store.journal("RUN_PUBLISHED", {"run_id": run_id, "fill_date": run["fill_date"], "lines": len(run["ticket"])})
    return store.get_run(run_id)


PRICE_SANITY = 0.20      # a fill more than 20% from the ticket's reference price is worth a second look


class PriceWarnings(RunRefused):
    """Fill prices that need the manager's review (not this week's traded range, or far from the reference). The
    request is refused without writing anything; resubmitting with confirm=True saves it as entered."""

    def __init__(self, warnings: list[dict]):
        self.warnings = warnings
        super().__init__("; ".join(f"{w['line_id']} {w['symbol']}: {w['reason']}" for w in warnings))


def _whole(label: str, name: str, v) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise RunRefused(f"{label}: {name} must be a whole number of shares, 0 or more")
    return v


def _positive_price(label: str, name: str, v) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        raise RunRefused(f"{label}: {name} must be a positive price")
    return float(v)


def _fill(lid: str, l: dict, it: dict) -> dict:
    """done = full ticket quantity · partial = strictly between 0 and it · extra = more than it (BUY only; the fund
    counts the ticket quantity, the surplus is an other holding) · skipped."""
    status = it.get("status")
    if status not in ("done", "partial", "extra", "skipped"):
        raise RunRefused(f"{lid}: status must be done, partial, extra or skipped")
    if status == "skipped":
        return {"status": status, "qty": 0, "price": None}
    price = it.get("price")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        raise RunRefused(f"{lid}: a fill price is required")
    price, ticket_qty, q = float(price), int(l["qty"]), it.get("qty")
    if status == "done":
        return {"status": status, "qty": ticket_qty, "price": price}
    if status == "partial":
        if isinstance(q, bool) or not isinstance(q, int) or not 0 < q < ticket_qty:
            raise RunRefused(f"{lid}: a partial fill must be between 1 and {ticket_qty - 1} shares")
        return {"status": status, "qty": q, "price": price}
    if l["side"] != "BUY":
        raise RunRefused(f"{lid}: extra applies to BUY lines only")
    if isinstance(q, bool) or not isinstance(q, int) or q <= ticket_qty:
        raise RunRefused(f"{lid}: an extra fill must be more than the ticket's {ticket_qty} shares")
    return {"status": status, "qty": q, "price": price, "extra_qty": q - ticket_qty}


def _price_warning(l: dict, price: float, rng) -> str | None:
    reasons = []
    if rng and not rng[0] <= price <= rng[1]:
        reasons.append(f"not traded at this price since the fill date (low {rng[0]:.2f}, high {rng[1]:.2f}) — "
                       f"an older holding's cost or a later buy?")
    if abs(price / l["ref_px"] - 1) > PRICE_SANITY:
        reasons.append(f"more than 20% from the reference {l['ref_px']:.2f} — check for a typo")
    return "; ".join(reasons) or None


def record_executions(store, run_id: str, items: list[dict], price_range=None, confirm: bool = False) -> dict:
    """Record what the manager actually did per ticket line (statuses: see `_fill`).

    `price_range(symbols, since_iso) -> {symbol: (low, high)}` is the traded range from the fill date to the latest
    bar. A price outside it, or more than 20% from the reference, raises PriceWarnings unless `confirm`; confirmed
    records keep the warning text as `price_warning`. Validates the whole request before writing anything."""
    with _write_lock:
        run = store.get_run(run_id)
        if not run:
            raise RunRefused("run not found")
        if run["status"] not in ("PUBLISHED", "EXECUTING", "CLOSED"):
            raise RunRefused("executions can be recorded only on a published run")
        lines = {l["line_id"]: l for l in run["ticket"]}
        seen, recs = set(), []
        for it in items:
            lid = it.get("line_id")
            l = lines.get(lid)
            if l is None:
                raise RunRefused(f"unknown ticket line {lid}")
            if lid in seen:
                raise RunRefused(f"{lid} appears more than once in this request")
            seen.add(lid)
            recs.append((l, {"line_id": lid, "sleeve": l["sleeve"], "symbol": l["symbol"], "side": l["side"],
                             **_fill(lid, l, it), "note": str(it.get("note", ""))[:200]}))
        priced = [r["symbol"] for _, r in recs if r["price"] is not None]
        ranges = price_range(priced, run.get("fill_date")) if price_range and priced else {}
        warnings = []
        for l, r in recs:
            w = _price_warning(l, r["price"], ranges.get(r["symbol"])) if r["price"] is not None else None
            if w:
                rng = ranges.get(r["symbol"]) or (None, None)
                warnings.append({"line_id": r["line_id"], "symbol": r["symbol"], "price": r["price"],
                                 "ref_px": l["ref_px"], "low": rng[0], "high": rng[1], "reason": w})
                r["price_warning"] = w
        if warnings and not confirm:
            raise PriceWarnings(warnings)
        for _, r in recs:
            store.record_execution(run_id, r)
        done = {e["line_id"] for e in store.executions(run_id)}
        status = "CLOSED" if done >= set(lines) else "EXECUTING"
        store.set_status(run_id, status)
        store.journal("EXECUTIONS_RECORDED", {"run_id": run_id, "lines": [r["line_id"] for _, r in recs],
                                              "run_status": status,
                                              "confirmed_warnings": [w["line_id"] for w in warnings]})
        return {"run_id": run_id, "status": status, "recorded": len(done), "lines": len(lines)}


def search_symbols(q: str, universe: list[dict], limit: int = 12) -> list[dict]:
    """Rank `universe` ([{symbol, name}]) for a symbol input: exact symbol, then symbol prefix, then symbol substring,
    then company-name substring; alphabetical within a tier. Plain case-insensitive text matching (no regex), so
    symbols like M&M work and '.*' matches nothing. Duplicate symbols keep their first name."""
    needle = str(q or "").strip().upper()
    if not needle:
        return []
    seen, tiers = set(), ([], [], [], [])
    for row in universe:
        sym = str(row.get("symbol") or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        name = str(row.get("name") or "").strip()
        if sym == needle:
            t = 0
        elif sym.startswith(needle):
            t = 1
        elif needle in sym:
            t = 2
        elif needle in name.upper():
            t = 3
        else:
            continue
        tiers[t].append({"symbol": sym, "name": name or sym})
    out = [r for tier in tiers for r in sorted(tier, key=lambda r: r["symbol"])]
    return out[:max(1, int(limit))]


def set_other_holdings(store, items: dict, known) -> list[dict]:
    """Replace the other (outside-the-books) holding of each given symbol: {SYMBOL: {qty, cost_basis?, note?}};
    qty 0 removes it. `known(symbol)` must confirm the symbol exists in the market database. Optional `sleeve` tags
    the holding to a configured sleeve so Tracking counts it toward that sleeve (absent/None = untagged; the whole
    doc is replaced, so an edit that omits it untags). Validates everything before writing."""
    with _write_lock:
        clean, labels = {}, None
        for sym, o in items.items():
            sym = str(sym).strip().upper()
            if not known(sym):
                raise RunRefused(f"unknown symbol {sym}")
            o = o or {}
            q = _whole(sym, "qty", o.get("qty"))
            cost = _positive_price(sym, "cost_basis", o.get("cost_basis"))
            note = str(o["note"]).strip()[:200] if o.get("note") else None
            sleeve = str(o["sleeve"]).strip() if o.get("sleeve") else None
            if sleeve is not None:
                labels = labels or {s["label"] for s in store.get_config()["sleeves"]}
                if sleeve not in labels:
                    raise RunRefused(f"{sym}: sleeve must be one of {sorted(labels)}")
            clean[sym] = None if q == 0 else {k: v for k, v in (("qty", q), ("cost_basis", cost), ("note", note),
                                                                ("sleeve", sleeve)) if v is not None}
        for sym, doc in clean.items():
            store.set_other_holding(sym, doc)
            store.journal("OTHER_HOLDING_UPDATED", {"symbol": sym, **(doc or {"qty": 0})})
        return store.other_holdings()


def add_cashflow(store, amount, note: str = "") -> dict:
    """Record a deposit (+) or withdrawal (-) after go-live; the next published run applies it."""
    with _write_lock:
        cfg = store.get_config()
        if not store.published_runs(cfg["version"]):
            raise RunRefused("the fund is not live yet — before go-live, set the amount as Capital in Settings")
        last = store.db.fund_runs.find_one({"version": cfg["version"], "status": {"$ne": "DISCARDED"}},
                                           {"fund": 1}, sort=[("created_at", -1)])
        value = float((last or {}).get("fund", {}).get("value") or cfg["capital"])
        pending = sum(f["amount"] for f in store.cashflows(cfg["version"]) if f["status"] == "PENDING")
        err = validate_flow(amount, value + pending)
        if err:
            raise RunRefused(err)
        doc = store.add_cashflow(cfg["version"], float(amount), str(note)[:200])
        store.journal("CASHFLOW_ADDED", {"id": doc["_id"], "amount": doc["amount"], "note": doc["note"]})
        return doc


def cancel_cashflow(store, flow_id: str) -> None:
    with _write_lock:
        if not store.cancel_cashflow(flow_id):
            raise RunRefused("only a pending cash flow can be cancelled")
        store.journal("CASHFLOW_CANCELLED", {"id": flow_id})


def _fund_qty(e: dict) -> int:
    """Shares an execution put into (or took out of) the fund; an `extra` surplus is an other holding."""
    return e["qty"] - int(e.get("extra_qty") or 0)


def _published_executions(store) -> list[dict]:
    cfg = store.get_config()
    ids = {r["_id"] for r in store.published_runs(cfg["version"])}
    return [e for e in store.executions() if e["run_id"] in ids and e["status"] != "skipped" and e.get("qty")]


def actual_positions(store) -> dict[tuple, int]:
    """{(sleeve label, symbol): shares the manager holds in the fund}: published ticket executions plus manual trades
    (overlay sells/buys recorded outside a ticket) of the current model version."""
    cfg = store.get_config()
    pos: dict[tuple, int] = {}
    for e in _published_executions(store):
        k = (e["sleeve"], e["symbol"])
        pos[k] = pos.get(k, 0) + (1 if e["side"] == "BUY" else -1) * _fund_qty(e)
    for t in store.manual_trades(cfg["version"]):
        k = (t["sleeve"], t["symbol"])
        pos[k] = pos.get(k, 0) + (1 if t["side"] == "BUY" else -1) * int(t["qty"])
    return pos


def record_manual_trade(store, t: dict, known) -> dict:
    """A trade made outside a ticket (e.g. the streak-watch overlay: sell a value-core holding that broke its 222-SMA,
    buy Grid-10's next pick). Counted by Tracking; the model is not changed. Refuses an unknown sleeve or symbol,
    non-positive whole quantities or prices, and selling more than the sleeve holds."""
    with _write_lock:
        cfg = store.get_config()
        labels = {s["label"] for s in cfg["sleeves"]}
        sleeve = t.get("sleeve")
        if sleeve not in labels:
            raise RunRefused(f"sleeve must be one of {sorted(labels)}")
        sym = str(t.get("symbol", "")).strip().upper()
        if not known(sym):
            raise RunRefused(f"unknown symbol {sym}")
        side = t.get("side")
        if side not in ("BUY", "SELL"):
            raise RunRefused("side must be BUY or SELL")
        q = t.get("qty")
        if isinstance(q, bool) or not isinstance(q, int) or q <= 0:
            raise RunRefused("qty must be a whole number of shares above 0")
        px = t.get("price")
        if isinstance(px, bool) or not isinstance(px, (int, float)) or not math.isfinite(px) or px <= 0:
            raise RunRefused("price must be a positive number")
        if side == "SELL":
            have = actual_positions(store).get((sleeve, sym), 0)
            if q > have:
                raise RunRefused(f"{sleeve} holds {have} {sym}; cannot sell {q}")
        doc = store.add_manual_trade({"version": cfg["version"], "sleeve": sleeve, "symbol": sym, "side": side,
                                      "qty": q, "price": float(px), "note": str(t.get("note") or "")[:200],
                                      "trade_date": str(t.get("trade_date") or date.today())[:10]})
        store.journal("MANUAL_TRADE_RECORDED", {k: doc[k] for k in ("sleeve", "symbol", "side", "qty", "price", "note")})
        return doc


def delete_manual_trade(store, trade_id: str) -> None:
    with _write_lock:
        if not store.delete_manual_trade(trade_id):
            raise RunRefused("manual trade not found")
        store.journal("MANUAL_TRADE_DELETED", {"id": trade_id})


def other_holdings_view(store, price_of=None) -> dict:
    """Everything held outside the books: manual entries plus the surplus of `extra` fills on published tickets,
    valued at the latest close where known."""
    manual = store.other_holdings()
    extras = [e for e in _published_executions(store) if e.get("extra_qty")]
    syms = sorted({o["symbol"] for o in manual} | {e["symbol"] for e in extras})
    closes = price_of(syms) if price_of and syms else {}

    def valued(row: dict) -> dict:
        px = closes.get(row["symbol"])
        return {**row, "price": px, "value": row["qty"] * px if px else None}

    rows_manual = [valued({"symbol": o["symbol"], "qty": o["qty"], "cost_basis": o.get("cost_basis"),
                           **({"note": o["note"]} if o.get("note") else {}),
                           **({"sleeve": o["sleeve"]} if o.get("sleeve") else {})}) for o in manual]
    rows_tickets = [valued({"symbol": e["symbol"], "qty": int(e["extra_qty"]), "cost_basis": e["price"],
                            "source": f"{e['run_id']} {e['line_id']}"})
                    for e in sorted(extras, key=lambda e: (e["symbol"], e["run_id"]))]
    return {"manual": rows_manual, "from_tickets": rows_tickets,
            "value": sum(r["value"] for r in rows_manual + rows_tickets if r["value"] is not None)}


def actual_book(store, price_of=None) -> dict:
    """Positions implied by execution records, compared with the latest run's model book (in whole shares).

    Valued at the latest close from `price_of(symbols) -> {symbol: close}` where available, else the model book's
    price. Adds a per-sleeve summary (model / actual / under-invested / over-invested rupees), the capital not
    deployed and the value of other holdings (outside the books), which are never counted as gaps.

    Other holdings tagged to a sleeve are tracking-only money for that sleeve (fund NAV is untouched): a tag on a
    stock the model holds in that sleeve first fills its share gap (row `tagged_qty`); the rest counts by value
    (`tagged_value`, listed in `tagged`). `to_deploy` = under-invested minus tagged value, floored at 0, and
    `undeployed` sums it — the next ticket does not buy that back on its own."""
    cfg = store.get_config()
    pos = {k: {"qty": q} for k, q in actual_positions(store).items()}
    latest = store.latest_run(cfg["version"])
    model = {}
    if latest:
        for b in latest["books"]:
            for h in b["holdings"]:
                model[(b["label"], h["symbol"])] = {"shares": h["shares"], "price": h["price"]}
    tags = [o for o in store.other_holdings() if o.get("sleeve")]
    keys = sorted(set(pos) | set(model))
    syms = sorted({k[1] for k in keys} | {o["symbol"] for o in tags})
    closes = price_of(syms) if price_of and syms else {}
    tag_qty = {(o["sleeve"], o["symbol"]): int(o["qty"]) for o in tags}
    rows, sleeves, surplus = [], {}, {}

    def sleeve(label):
        return sleeves.setdefault(label, {"sleeve": label, "model_value": 0.0, "actual_value": 0.0, "under_value": 0.0,
                                          "over_value": 0.0, "tagged_value": 0.0, "to_deploy": 0.0, "unpriced": 0})

    for k in keys:
        a = pos.get(k, {"qty": 0})
        mdl = model.get(k, {"shares": 0.0, "price": None})
        px = closes.get(k[1]) or mdl["price"]
        m_qty = round(mdl["shares"])
        used = min(tag_qty.get(k, 0), max(m_qty - a["qty"], 0)) if k in model else 0
        if k in tag_qty:
            surplus[k] = tag_qty[k] - used
        held = a["qty"] + used
        gap = held - m_qty
        rows.append({"sleeve": k[0], "symbol": k[1], "actual_qty": held, "model_qty": m_qty, "tagged_qty": used,
                     "gap_qty": gap, "gap_value": gap * px if px else None, "price": px})
        s = sleeve(k[0])
        if not px:
            s["unpriced"] += 1
            continue
        s["model_value"] += m_qty * px
        s["actual_value"] += held * px
        s["under_value"] += max(-gap, 0) * px
        s["over_value"] += max(gap, 0) * px
    tagged = []
    for k, q in sorted(tag_qty.items()):
        q = surplus.get(k, q)
        if q <= 0:
            continue
        px = closes.get(k[1])
        s = sleeve(k[0])
        tagged.append({"sleeve": k[0], "symbol": k[1], "qty": q, "price": px, "value": q * px if px else None})
        if px:
            s["tagged_value"] += q * px
        else:
            s["unpriced"] += 1
    for s in sleeves.values():
        s["to_deploy"] = max(s["under_value"] - s["tagged_value"], 0.0)
    sl = sorted(sleeves.values(), key=lambda s: s["sleeve"])
    other = other_holdings_view(store, price_of)
    untagged = [r for r in other["manual"] if not r.get("sleeve")] + other["from_tickets"]
    return {"as_of_run": latest["_id"] if latest else None, "rows": rows, "tagged": tagged,
            "tracking_value": sum(abs(r["gap_value"]) for r in rows if r["gap_value"] is not None),
            "sleeves": sl, "undeployed": sum(s["to_deploy"] for s in sl),
            "tagged_value": sum(s["tagged_value"] for s in sl),
            "other_value": sum(r["value"] for r in untagged if r["value"] is not None)}


# ------------------------------------------------------------------------------------------------- performance
# closest stored index per sleeve universe (no Nifty 200 / Total Market series in the database)
INDEX_BENCH = {"NIFTY50": "Nifty 50", "NIFTY100": "Nifty 100", "NIFTY200": "Nifty 100", "NIFTY500": "Nifty 500",
               "NIFTY500MICRO750": "Nifty 500", "NIFTYMIDCAP150": "Nifty Midcap 150",
               "NIFTYSMALLCAP250": "Nifty Smallcap 250", "NIFTYMICROCAP250": "Nifty Microcap 250"}
PERF_TTL_S = 900
_perf_cache: dict = {}
_perf_lock = threading.Lock()


def _round(xs, nd=4):
    return [None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), nd) for x in xs]


def _closes(m, d, symbols) -> dict[str, float]:
    """Close of each symbol on session `d` (forward-filled, so a halted stock keeps its last price); unpriced left out."""
    row = m.cclose.loc[d]
    return {s: float(row[s]) for s in symbols if s in row.index and not pd.isna(row[s])}


def _index_series(db, name: str, ms: date) -> pd.Series:
    from .data import load_from, load_prices
    _, closes = load_prices(db, [name], load_from(ms))[:2]
    if name not in closes.columns or closes[name].dropna().empty:
        raise RunRefused(f"no prices stored for benchmark {name!r}")
    return closes[name]


def performance(db, store, today: date | None = None, extra_start: date | None = None) -> dict:
    """The model fund and each book replayed from model start to the latest close, with the exact schedule the runs
    use (see _plan), shown for two periods: since model start, and the current calendar year. Model figures, not the
    manager's fills. Cached per (version, config, published runs, latest close) for PERF_TTL_S seconds.
    `extra_start` adds a third period from the last close on or before that date (uncached; used by tests)."""
    from . import performance as P
    today = today or date.today()
    cfg = store.get_config()
    errs = validate(cfg, today)
    if errs:
        raise RunRefused("; ".join(errs))
    published = store.published_runs(cfg["version"])
    key = (cfg["version"], cfg.get("updated_at"), tuple(r["_id"] for r in published), str(latest_bench_date(db)),
           today.isoformat())
    with _perf_lock:
        hit = _perf_cache.get(key)
        if extra_start is None and hit and (datetime.now(timezone.utc).timestamp() - hit[0]) < PERF_TTL_S:
            return hit[1]

    ms = date.fromisoformat(cfg["model_start"])
    cap = float(cfg["capital"])
    costs = Costs(*charge_rates(), float(cfg["slippage_bp"]) / 1e4)
    markets = {sl["id"]: _market(db, sl, ms, today) for sl in cfg["sleeves"]}
    bench = next(iter(markets.values())).closes[BENCH]
    pl = _plan(cfg, published, bench, tuple(sorted(market_holidays(db))))
    S = pl["S"]

    # Every curve is anchored at its starting capital (1.0) on the close before model start: the replay's first point
    # is day 1's close, after the opening build, so without the anchor day 1's move would be missing from returns
    # while present in P&L.
    anchor = bench.loc[:pd.Timestamp(ms) - pd.Timedelta(days=1)].dropna().index[-1]
    curves, orders = {}, {}
    for sl in cfg["sleeves"]:
        res = replay_sleeve(_book(sl), markets[sl["id"]], costs, ms, S, pl["scheds"][sl["id"]], 1.0)
        curves[sl["id"]] = pd.concat([pd.Series([1.0], index=[anchor]), res.equity])
        orders[sl["id"]] = res.orders
    weights = {sl["id"]: float(sl["weight"]) for sl in cfg["sleeves"]}
    idx = curves[next(iter(curves))].index
    px = pd.DataFrame({k: v.reindex(idx).ffill() for k, v in curves.items()})
    first = {idx[1]}                     # the engine never rebalances on its first session (it starts at target)
    fr = blend(px, weights, float(cfg["reset_cost"]), reset_days=pl["reset_days"] - first,
               band_days=pl["first_run_days"] - first, band=pl["band"])

    nifty = bench.reindex(idx).ffill()
    fund_curve = cap * fr.equity
    wnow = fr.sleeve_values.iloc[-1] / fr.sleeve_values.iloc[-1].sum()
    labels = {sl["id"]: sl["label"] for sl in cfg["sleeves"]}

    # per-sleeve inputs that do not depend on the period: benchmark series and the full position history
    static = {}
    for sl in cfg["sleeves"]:
        sid, m = sl["id"], markets[sl["id"]]
        st = {"index_name": "Nifty 50", "index": nifty, "ew": None, "pos": None}
        if sl["kind"] != "asset":
            st["index_name"] = INDEX_BENCH.get(sl.get("universe"), BENCH)
            st["index"] = nifty if st["index_name"] == BENCH else _index_series(db, st["index_name"], ms)
            st["ew"] = P.ew_universe(m, curves[sid].index)
            traded = sorted({o.symbol for os in orders[sid].values() for o in os})
            st["traded"] = traded
            st["pos"] = P.positions(orders[sid], _closes(m, S, traded))
        static[sid] = st

    def monthly(**series):
        rows: dict[str, dict] = {}
        for name, x in series.items():
            for r in P.monthly_returns(x):
                rows.setdefault(r["m"], {"m": r["m"]})[name] = r["r"]
        return [rows[k] for k in sorted(rows)]

    def window(lo: pd.Timestamp) -> tuple[dict, list]:
        """Everything on the page for the period from the close of `lo` to S."""
        fc, nf = fund_curve.loc[lo:], nifty.loc[lo:]
        contrib, cost_pts = P.contribution(px.loc[lo:], fr.sleeve_values.loc[lo:])
        sv = fr.sleeve_values.loc[lo:]
        fund = {
            "stats": P.series_stats(fc),
            "relative": [P.relative(fc, nf, "Nifty 50", "index")],
            "curve": {"dates": [str(d.date()) for d in fc.index], "fund": _round(fc, 0),
                      "nifty": _round(float(fc.iloc[0]) * nf / float(nf.dropna().iloc[0]), 0)},
            "drawdown": {"fund": _round(P.drawdown(fc), 3), "nifty": _round(P.drawdown(nf), 3)},
            "monthly": monthly(fund=fc, nifty=nf),
            "contribution": [{"id": k, "label": labels[k], "pts": v} for k, v in contrib.items()],
            "reset_cost_pts": cost_pts,
            "resets": [str(pd.Timestamp(d).date()) for d in fr.reset_days if pd.Timestamp(d) > lo],
            "weights": {k: _round(100 * sv[k] / sv.sum(axis=1), 3) for k in weights},
        }
        books = []
        for sl in cfg["sleeves"]:
            sid, m, st = sl["id"], markets[sl["id"]], static[sl["id"]]
            c = curves[sid].loc[lo:].dropna()
            series = {"book": c, "index": st["index"]}
            rel = [P.relative(c, st["index"], st["index_name"], "index")]
            if st["ew"] is not None:
                series["ew"] = st["ew"]
                rel.append(P.relative(c, st["ew"], f"Equal-weight {sl.get('universe')}", "ew"))
            trades = None
            if st["pos"] is not None:
                v0, v1 = float(c.iloc[0]), float(c.iloc[-1])
                trades = P.trade_summary(st["pos"], base=v0, closed_after=None if lo == anchor else str(lo.date()))
                for p_ in trades["open"]:
                    p_["weight"] = 100 * p_.pop("value") / v1
                    p_.pop("qty")
                trades["orders"] = sum(len(os) for d, os in orders[sid].items() if pd.Timestamp(d) > lo)
                trades["turnover"] = P.turnover(orders[sid], c)
                trades["pnl_by_symbol"] = P.symbol_pnl(orders[sid], lo, _closes(m, lo, st["traded"]),
                                                       _closes(m, S, st["traded"]), base=v0)
            books.append({
                "id": sid, "label": sl["label"], "kind": sl["kind"], "universe": sl.get("universe"),
                "symbol": sl.get("symbol"), "index_name": st["index_name"],
                "target_weight": 100 * weights[sid], "weight": 100 * float(wnow[sid]), "contribution_pts": contrib[sid],
                "stats": P.series_stats(c), "relative": rel,
                "curve": P.series_payload(c.index, **series),
                "drawdown": {k: _round(P.drawdown(v.reindex(c.index).ffill()), 3) for k, v in series.items()},
                "monthly": monthly(**series),
                "trades": trades,
            })
        return fund, books

    # periods: the whole model, and the current calendar year (from the last close of the previous year; when the
    # model started this year the two are the same period and are flagged so)
    prior = bench.loc[:pd.Timestamp(S.year - 1, 12, 31)].dropna()
    ytd_lo = max(prior.index[-1], anchor) if len(prior) else anchor
    wanted = [("all", "Since model start", anchor), ("ytd", f"{S.year} to date", ytd_lo)]
    if extra_start is not None:
        lo = bench.loc[:pd.Timestamp(extra_start)].dropna().index[-1]
        if not anchor < lo < S:
            raise RunRefused(f"extra_start must fall inside the model's history ({anchor.date()} .. {S.date()})")
        wanted.append(("custom", f"From {lo.date()}", lo))
    periods = []
    for pid, label, lo in wanted:
        f_, b_ = window(lo)
        periods.append({"id": pid, "label": label, "start": str(lo.date()), "same_as_all": pid != "all" and lo == anchor,
                        "fund": f_, "books": b_})

    out = {"as_of": str(S.date()), "model_start": cfg["model_start"], "version": cfg["version"], "capital": cap,
           "risk_free": 0.0, "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "periods": periods}
    if extra_start is not None:
        return out
    with _perf_lock:
        _perf_cache.clear()
        _perf_cache[key] = (datetime.now(timezone.utc).timestamp(), out)
    return out
