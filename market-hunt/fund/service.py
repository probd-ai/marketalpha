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
from .config import validate
from .data import BENCH, build_asset_market, build_market, charge_rates
from .engine import ENGINE_VERSION
from .engine.asset import Asset
from .engine.calendar import period_start_shifted, trading_days, week_session
from .engine.costs import Costs
from .engine.fund import blend
from .engine.grid10 import Grid10
from .engine.projection import next_session, project
from .engine.replay import replay_sleeve
from .engine.tranche import Tranche
from .engine.types import Order, Reason, SleeveState
from .flows import resize_factors, total_units, validate_flow
from .ticket import build_ticket


class RunRefused(Exception):
    """A business rule prevents the action (shown to the manager verbatim)."""


def _book(sl: dict):
    p = sl.get("params") or {}
    if sl["kind"] == "grid10":
        return Grid10(int(p.get("n", 10)))
    if sl["kind"] == "tranche":
        return Tranche(int(p.get("top", 5)), int(p.get("weeks", 8)), int(p.get("slot_div", 40)),
                       float(p.get("max_w", 0.10)), bool(p.get("deploy_cash", True)))
    return Asset(sl["symbol"])


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
    pub_fill = sorted(pd.Timestamp(r["fill_date"]) for r in published)
    first_pub = pub_fill[0] if pub_fill else None

    markets = {}
    for sl in cfg["sleeves"]:
        progress(f"loading {sl['label']}")
        markets[sl["id"]] = _market(db, sl, ms, today)
    bench = next(iter(markets.values())).closes[BENCH]
    S = bench.dropna().index[-1]
    F = next_session(S)
    week = list(F.isocalendar()[:2])
    if any(list(pd.Timestamp(r["fill_date"]).isocalendar()[:2]) == week for r in published):
        raise RunRefused(f"a run is already published for ISO week {week[0]}-W{week[1]:02d}")
    cal = trading_days(bench, ms, S)
    in_f_week = lambda d: list(d.isocalendar()[:2]) == week
    k = int(cfg.get("backfill_weekday", 0))

    def schedule(kind):
        if kind == "asset":
            return set()
        base = period_start_shifted(cal, "weekly", k) if kind == "grid10" else week_session(cal, k)
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

    # replay each sleeve through the published checkpoints to S, then one projected step to F
    cps = [pd.Timestamp(r["signal_date"]) for r in published]
    curves, st_S, st_F, orders_F, mismatches = {}, {}, {}, {}, []
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
        tsl[sl["label"]] = {"orders": orders_F[sid], "scale": scale[sid],
                            "held_before": {s: st_S[sid].qty(s) for s in st_S[sid].lots},
                            "held_after": after,
                            "reset_factor": resize[sid] if resize else None, "resize_reasons": resize_reasons,
                            "ctx": ctx}
    lines, residual = build_ticket(tsl, ref, float(cfg["limit_band"]))
    tick = [l.to_dict() for l in lines]

    # checks
    ck = []
    ind_days = [m.indicator_last.max() for m in markets.values() if m.indicator_last is not None]
    ck.append(C.freshness(S.date(), today, min(ind_days).date() if ind_days else None))
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


PRICE_SANITY = 0.20      # a recorded fill more than 20% away from the ticket's reference price is treated as a typo


def record_executions(store, run_id: str, items: list[dict]) -> dict:
    """Record what the manager actually did per ticket line. `done` always means the full ticket quantity; `partial`
    needs a quantity strictly between 0 and the ticket quantity; every fill needs a finite price within ±20% of the
    reference. Validates the whole request before writing anything."""
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
            status = it.get("status")
            if status not in ("done", "partial", "skipped"):
                raise RunRefused(f"{lid}: status must be done, partial or skipped")
            qty, price = 0, None
            if status != "skipped":
                price = it.get("price")
                if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
                    raise RunRefused(f"{lid}: a fill price is required")
                price = float(price)
                if abs(price / l["ref_px"] - 1) > PRICE_SANITY:
                    raise RunRefused(f"{lid}: fill price {price:.2f} is more than 20% away from the reference "
                                     f"{l['ref_px']:.2f} — check for a typo")
                if status == "done":
                    qty = int(l["qty"])
                else:
                    q = it.get("qty")
                    if isinstance(q, bool) or not isinstance(q, int) or not 0 < q < l["qty"]:
                        raise RunRefused(f"{lid}: a partial fill must be between 1 and {l['qty'] - 1} shares")
                    qty = q
            recs.append({"line_id": lid, "sleeve": l["sleeve"], "symbol": l["symbol"], "side": l["side"],
                         "status": status, "qty": qty, "price": price, "note": str(it.get("note", ""))[:200]})
        for r in recs:
            store.record_execution(run_id, r)
        done = {e["line_id"] for e in store.executions(run_id)}
        status = "CLOSED" if done >= set(lines) else "EXECUTING"
        store.set_status(run_id, status)
        store.journal("EXECUTIONS_RECORDED", {"run_id": run_id, "lines": [r["line_id"] for r in recs],
                                              "run_status": status})
        return {"run_id": run_id, "status": status, "recorded": len(done), "lines": len(lines)}


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


def actual_book(store) -> dict:
    """Positions implied by execution records, compared with the latest run's model book (in whole shares)."""
    cfg = store.get_config()
    runs = store.published_runs(cfg["version"])
    ids = {r["_id"] for r in runs}
    pos: dict[tuple, dict] = {}
    for e in store.executions():
        if e["run_id"] not in ids or e["status"] == "skipped" or not e.get("qty"):
            continue
        k = (e["sleeve"], e["symbol"])
        p = pos.setdefault(k, {"sleeve": e["sleeve"], "symbol": e["symbol"], "qty": 0, "cost": 0.0})
        sign = 1 if e["side"] == "BUY" else -1
        p["qty"] += sign * e["qty"]
        p["cost"] += sign * e["qty"] * e["price"]
    latest = store.db.fund_runs.find_one({"version": cfg["version"]}, sort=[("created_at", -1)])
    model = {}
    if latest:
        for b in latest["books"]:
            for h in b["holdings"]:
                model[(b["label"], h["symbol"])] = {"shares": h["shares"], "price": h["price"]}
    rows = []
    for k in sorted(set(pos) | set(model)):
        a = pos.get(k, {"qty": 0, "cost": 0.0})
        mdl = model.get(k, {"shares": 0.0, "price": None})
        px = mdl["price"]
        rows.append({"sleeve": k[0], "symbol": k[1], "actual_qty": a["qty"], "model_qty": round(mdl["shares"]),
                     "gap_qty": a["qty"] - round(mdl["shares"]),
                     "gap_value": (a["qty"] - round(mdl["shares"])) * px if px else None, "price": px})
    return {"as_of_run": latest["_id"] if latest else None, "rows": rows,
            "tracking_value": sum(abs(r["gap_value"]) for r in rows if r["gap_value"] is not None)}
