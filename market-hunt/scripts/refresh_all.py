"""refresh_all.py — single-command market-hunt data refresh pipeline.

Stages (in dependency order, each skippable):
  urls        Re-pull index/ETF constituents from saved URL configs (rebalance refresh).
              Requires the FastAPI backend on :3001 (url_loader.py drives it).
  mappings    Refresh symbol -> NSE scrip-code mappings from index_meta.
  prices      Incremental OHLCV top-up for the FULL priced universe:
              distinct symbols already priced UNION current constituents of --universes.
              One built-in retry pass (tvdatafeed transient failures are ~5-10%/pass).
  recon       Reconstitution freshness: replay-consistency of index_reconstitution vs the
              freshly pulled constituents, plus detection of niftyindices press releases
              not yet captured. REPORTS ONLY — capturing a PR is a verified, human/LLM
              procedure (docs/capture_index_rebalance.md); this stage never writes.
  indicators  Full TrueVX recompute (compute_indicators.py --force; the engine has no
              incremental mode — without --force it skips symbols entirely).
  report      Per-symbol freshness distributions; exit 1 if staleness exceeds tolerance.

Usage (from market-hunt repo root):
    .venv/Scripts/python.exe scripts/refresh_all.py                # everything
    .venv/Scripts/python.exe scripts/refresh_all.py --skip urls    # market closed midweek
    .venv/Scripts/python.exe scripts/refresh_all.py --only recon,report
    .venv/Scripts/python.exe scripts/refresh_all.py --stale-tolerance 25
"""
from __future__ import annotations

import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import argparse
import asyncio
import datetime as dt
import json
import re
import subprocess
import sys
from collections import Counter

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "market_hunt"
BACKEND = _os.environ.get("MH_API", "http://localhost:3001")
PRESS_RELEASE_PAGE = "https://www.niftyindices.com/press-release"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Universes whose current constituents define the download set (plus already-priced symbols).
DEFAULT_UNIVERSES = ["NIFTYTOTALMARKET", "ETF", "MARKET INDEXES"]

# index_reconstitution index_name -> index_meta index_name, for the consistency check.
TRACKED_RECON = {
    "NIFTY50": "NIFTY50", "NIFTY100": "NIFTY100", "NIFTY200": "NIFTY200",
    "NIFTY500": "NIFTY500", "NIFTYMIDCAP150": "NIFTYMIDCAP150",
    "NIFTYSMALLCAP250": "NIFTYSMALLCAP250", "NIFTYMICROCAP250": "NIFTYMICROCAP250",
}

STAGES = ["calendar", "urls", "mappings", "prices", "recon", "indicators", "report"]

# Corporate actions: the NSE feed is the primary detector, the price-jump scan is the
# backstop for what it misses (unauthenticated endpoint, short window, 403s under load).
CA_LOOKBACK_DAYS = 120          # > the feed's practical window, and covers a missed run
PRICE_JUMP_RATIO = 1.333        # prev/close beyond this (or its reciprocal) = an action
PRICE_JUMP_SCAN_BARS = 90       # trailing bars examined per symbol by the backstop

# PR titles that can move equity index membership; the exclusions are index families
# we never track (fixed income, SME, Shariah, thematic corporate-group, IPO-index).
PR_RELEVANT = re.compile(r"(?i)replace|inclusion|exclusion|change in constituent|corporate a(ction|djustment)")
PR_IRRELEVANT = re.compile(r"(?i)fixed income|sme emerge|shariah|nifty ipo|corporate group|aif|launches")


# --------------------------------------------------------------------------- pure logic
def pick_stages(only: str | None, skip: str | None) -> list[str]:
    chosen = [s.strip() for s in only.split(",")] if only else list(STAGES)
    skipped = {s.strip() for s in (skip or "").split(",") if s.strip()}
    unknown = (set(chosen) | skipped) - set(STAGES)
    if unknown:
        raise SystemExit(f"unknown stage(s): {sorted(unknown)}; valid: {STAGES}")
    return [s for s in chosen if s not in skipped]


def most_recent_trading_day(today: dt.date, holidays=()) -> dt.date:
    """Latest NSE session on or before `today`: weekends and the stored exchange holidays roll back."""
    from market_calendar import last_session
    return last_session(today, holidays)


def parse_pr_page(html: str) -> list[tuple[dt.date, str, str]]:
    """(release_date, pdf_url_path, title) for every press release on the page."""
    out, seen = [], set()
    for m in re.finditer(
            r"href=['\"]/?(?:https://www\.niftyindices\.com/)?(Press_Release/[A-Za-z0-9_]+\.pdf)['\"][^>]*>([^<]{3,200})<",
            html):
        link, title = m.group(1), m.group(2).strip()
        if link in seen:
            continue
        seen.add(link)
        dm = re.search(r"ind_prs(\d{2})(\d{2})(\d{4})", link)
        if not dm:
            continue
        out.append((dt.date(int(dm.group(3)), int(dm.group(2)), int(dm.group(1))), link, title))
    return out


def relevant_prs(prs: list[tuple[dt.date, str, str]], since: dt.date) -> list[tuple[dt.date, str, str]]:
    return sorted(
        (p for p in prs
         if p[0] >= since and PR_RELEVANT.search(p[2]) and not PR_IRRELEVANT.search(p[2])),
        key=lambda p: p[0])


def uncaptured_prs(candidates: list[tuple[dt.date, str, str]], stored_source_urls: set[str],
                   reviewed_names: set[str] = frozenset()) -> list[tuple[dt.date, str, str]]:
    """PRs neither captured as events nor logged as reviewed-no-impact."""
    known = {u.rsplit("/", 1)[-1] for u in stored_source_urls if u} | set(reviewed_names)
    return [p for p in candidates if p[1].rsplit("/", 1)[-1] not in known]


def check_recon_consistency(events: list[dict], current: set[str], today: dt.date) -> dict:
    """Compare stored reconstitution events for ONE index against its current members.

    Events are captured when ANNOUNCED — niftyindices publishes ~1 month before the
    change takes effect — so an event dated in the future is legitimately absent from
    the freshly pulled constituents. Comparing it would flag a correctly captured
    review on every run until it takes effect, so only effective events are compared.

    Returns the gate mismatch (most recent effective event vs today's members), the
    cumulative-replay gaps (historical debt, informational), and the newest effective
    effective_date seen.
    """
    gate, status, last_ev = {}, {}, None
    newest = None
    for ev in sorted(events, key=lambda e: e["effective_date"]):
        if _as_date(ev["effective_date"]) > today:
            continue
        newest = _as_date(ev["effective_date"])
        for s in ev.get("added") or []:
            status[s] = "in"
        for s in ev.get("removed") or []:
            status[s] = "out"
        last_ev = ev
    if last_ev:
        g_in = sorted(s for s in (last_ev.get("removed") or []) if s in current)
        g_out = sorted(s for s in (last_ev.get("added") or []) if s not in current)
        if g_in or g_out:
            gate = {"removed_but_present": g_in, "added_but_absent": g_out}
    bad_in = sorted(s for s, v in status.items() if v == "out" and s in current)
    bad_out = sorted(s for s, v in status.items() if v == "in" and s not in current)
    gaps = {"removed_but_present": bad_in, "added_but_absent": bad_out} if (bad_in or bad_out) else {}
    return {"gate_mismatch": gate, "historical_gap": gaps, "newest_effective": newest}


def _as_date(v) -> dt.date:
    return v.date() if isinstance(v, dt.datetime) else v


def split_by_corporate_action(symbols: list[str],
                              actions: list[dict]) -> tuple[list[str], list[str]]:
    """(full_refresh, incremental) — symbols with a price-affecting action need the
    WHOLE history re-downloaded, because the source re-prices it end to end."""
    affected = {a.get("symbol") for a in actions or []}
    full = [s for s in symbols if s in affected]
    incremental = [s for s in symbols if s not in affected]
    return full, incremental


def detect_price_discontinuities(bars: list[tuple], ratio: float = None) -> list[dict]:
    """Single-session moves too large to be a real trade — i.e. an unadjusted action.

    `bars` is ascending [(date, close)]. NSE circuit limits cap a cash-segment stock
    well below this, so a breach is a corporate action the feed did not tell us about.
    Deliberately blunt: the real crashes in our history (INDUSINDBK −27%, RECLTD −25%)
    sit below the bar, while every split we found is far above it.
    """
    ratio = ratio or PRICE_JUMP_RATIO
    hits, prev = [], None
    for d, c in bars or []:
        if not c or c <= 0:
            prev = None                      # a gap breaks the comparison chain
            continue
        if prev is not None:
            r = prev[1] / c                  # >1 price fell (split), <1 price rose
            if r >= ratio or r <= 1 / ratio:
                hits.append({"date": d, "prev_date": prev[0], "prev_close": prev[1],
                             "close": c, "ratio": r})
        prev = (d, c)
    return hits


def classify_price_failures(failures: dict[str, str]) -> dict:
    """Split download failures into 'blocked before the network' vs 'source has no data'.

    A symbol rejected for a missing/none scrip code never reached TradingView — that
    is a pipeline bug, not market attrition, and it must fail the stage however few
    symbols it hits. Genuine source gaps (delisted, merged, TV doesn't carry it) are
    expected and only counted.
    """
    blocked = sorted(s for s, err in failures.items() if "no nse mapping" in err.lower())
    gaps = sorted(set(failures) - set(blocked))
    return {"ok": not blocked, "blocked_by_mapping": blocked, "source_gaps": gaps}


def freshness_summary(symbol_last_dates: dict[str, dt.date], expected: dt.date,
                      tolerance: int) -> dict:
    """tolerance = max count of stale symbols (beyond permanently-dead ones) allowed."""
    hist = Counter(d.isoformat() for d in symbol_last_dates.values())
    stale = sorted(s for s, d in symbol_last_dates.items() if d < expected)
    return {"expected": expected.isoformat(), "total": len(symbol_last_dates),
            "stale": stale, "stale_count": len(stale), "ok": len(stale) <= tolerance,
            "histogram_top": hist.most_common(6)}


# --------------------------------------------------------------------------- helpers
def log(msg: str) -> None:
    print(msg, flush=True)


def run_step(argv: list[str]) -> int:
    env = dict(_os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return subprocess.call(argv, env=env)


PY = sys.executable


def backend_up() -> bool:
    import requests
    try:
        return requests.get(f"{BACKEND}/api/urls", timeout=10).status_code == 200
    except Exception:
        return False


# --------------------------------------------------------------------------- stages
def stage_calendar() -> dict:
    """NSE trading holidays (holiday-master, equity segment) -> Mongo market_holidays. The report's expected date and
    the fund desk's fill session both depend on it; a failed fetch keeps the previously stored calendar."""
    from pymongo import MongoClient

    import market_calendar as mc
    db = MongoClient(MONGO_URI)[DB_NAME]
    try:
        rows = mc.parse_holidays(asyncio.run(mc.fetch_holiday_master()))
    except Exception as e:  # network / NSE format change: report it, keep what is stored
        years = sorted(mc.years_covered(db))
        log(f"[calendar] fetch failed ({e!r}); stored calendar covers {years or 'nothing'}")
        return {"ok": dt.date.today().year in years, "error": repr(e), "years": years}
    n = mc.store_holidays(db, rows)
    years = sorted(mc.years_covered(db))
    upcoming = [f"{r['date']} {r['description']}" for r in rows if r["date"] >= dt.date.today()][:3]
    log(f"[calendar] stored {n} NSE trading holidays; years {years}; next: {upcoming}")
    return {"ok": dt.date.today().year in years, "stored": n, "years": years, "upcoming": upcoming}


def stage_urls() -> dict:
    if not backend_up():
        return {"ok": False, "error": f"backend not reachable at {BACKEND} — start it: "
                f"{PY} -m uvicorn api_server:app --port 3001"}
    rc = run_step([PY, "scripts/url_loader.py", "reload", "--all"])
    return {"ok": rc == 0, "exit": rc}


def stage_mappings() -> dict:
    rc = run_step([PY, "DataLoadManagement.py", "refresh-mappings"])
    return {"ok": rc == 0, "exit": rc}


async def _prices(universes: list[str]) -> dict:
    from pymongo import MongoClient
    from stock_data_manager import StockDataManager

    sync = MongoClient(MONGO_URI)[DB_NAME]
    targets: set[str] = set()
    for cname in sync.list_collection_names():
        if cname.startswith("prices_"):
            targets.update(sync[cname].distinct("symbol"))
    for u in universes:
        targets.update(d["Symbol"] for d in sync["index_meta"].find({"index_name": u}, {"Symbol": 1}))
    ordered = sorted(targets)
    log(f"[prices] target universe: {len(ordered)} symbols "
        f"(priced ∪ {', '.join(universes)})")

    # Corporate actions FIRST: a split/bonus makes the source re-price the whole
    # history, so those symbols need a full re-download. An incremental top-up would
    # splice adjusted bars onto unadjusted ones and manufacture a crash that never
    # happened — KIRLPNU's 1:2 split showed as -48.4% and drove truevx 50.0 -> 0.0.
    from nse_data_client import fetch_corporate_actions
    actions = await fetch_corporate_actions(
        from_date=dt.datetime.now() - dt.timedelta(days=CA_LOOKBACK_DAYS))
    forced, incremental = split_by_corporate_action(ordered, actions)
    if forced:
        log(f"[prices] {len(forced)} symbols have a price-affecting corporate action "
            f"-> FULL re-download: {forced}")
    else:
        log("[prices] no price-affecting corporate actions in the window"
            + ("" if actions is not None else " (feed unavailable)"))

    failed: dict[str, str] = {}
    async with StockDataManager() as mgr:
        async def one_pass(symbols: list[str], tag: str,
                           force: bool = False) -> dict[str, str]:
            errs: dict[str, str] = {}
            for i, sym in enumerate(symbols, 1):
                fetched = True
                try:
                    res = await mgr.download_historical_data_for_symbol(
                        symbol=sym, force_refresh=force)
                    if res and res.get("error"):
                        errs[sym] = str(res["error"])
                    elif res and str(res.get("message", "")).lower().startswith("data current"):
                        fetched = False  # freshness short-circuit — no TV request was made
                except Exception as exc:
                    errs[sym] = f"{type(exc).__name__}: {exc}"
                if i % 100 == 0:
                    log(f"[prices:{tag}] {i}/{len(symbols)} done, {len(errs)} errors")
                if fetched:
                    await asyncio.sleep(0.25)
            return errs

        failed = await one_pass(forced, "corp-action", force=True) if forced else {}
        failed.update(await one_pass(incremental, "pass1"))
        if failed:
            log(f"[prices] retrying {len(failed)} failures once "
                "(tvdatafeed transients recover on retry)")
            failed = await one_pass(sorted(failed), "retry")

    classified = classify_price_failures(failed)
    if classified["blocked_by_mapping"]:
        log(f"[prices] {len(classified['blocked_by_mapping'])} symbols were BLOCKED before "
            "reaching the price source (missing scrip-code mapping) — this is a pipeline "
            f"bug, not market attrition: {classified['blocked_by_mapping'][:15]}")
    if classified["source_gaps"]:
        log(f"[prices] {len(classified['source_gaps'])} symbols have no data at the source "
            "(delisted / merged / not carried by tvdatafeed)")

    # Backstop: REPORT ONLY. Real crashes and bonuses overlap in magnitude (IEX's 3:2
    # bonus and a genuine India VIX move are both ratio 1.42), so no threshold tells
    # them apart. Auto-refreshing on this would re-download INDUSINDBK's real -27%
    # crash on every run forever. Surface it; a human decides.
    since = dt.datetime.now() - dt.timedelta(days=PRICE_JUMP_SCAN_BARS * 2)
    bars: dict[str, list] = {}
    for cname in [c for c in sync.list_collection_names() if c.startswith("prices_")]:
        for d in sync[cname].find({"date": {"$gte": since}},
                                  {"_id": 0, "symbol": 1, "date": 1, "close_price": 1}):
            bars.setdefault(d["symbol"], []).append((d["date"].date(), d.get("close_price")))
    suspects = {}
    for sym, rows in bars.items():
        hits = detect_price_discontinuities(sorted(rows)[-PRICE_JUMP_SCAN_BARS:])
        if hits:
            suspects[sym] = [{"date": str(h["date"]), "prev_close": h["prev_close"],
                              "close": h["close"], "ratio": round(h["ratio"], 3)}
                             for h in hits]
    unexplained = {s: v for s, v in suspects.items() if s not in set(forced)}
    if unexplained:
        log(f"[prices] ⚠ {len(unexplained)} symbols show a price jump the corporate-action "
            f"feed did NOT explain — review (a real crash is fine; an unadjusted split is "
            f"not): {sorted(unexplained)[:15]}")
        log("[prices]   repair one with: download_historical_data_for_symbol("
            "symbol=SYM, force_refresh=True) then recompute its indicators")

    # A price fix alone is not enough: the phantom bar stays baked into stored
    # indicators for up to 222 sessions (the mean_long window), which is what actually
    # breaks selection. Re-derive them for every symbol we re-downloaded.
    reindexed = []
    if forced:
        ok = [s for s in forced if s not in failed]
        if ok:
            log(f"[prices] recomputing indicators for {len(ok)} corp-action symbols")
            rc = run_step([PY, "scripts/compute_indicators.py",
                           "--start-fetch", "2016-01-01", "--store-from", "2018-01-01",
                           "--force", "--symbols", ",".join(ok)])
            reindexed = ok if rc == 0 else []
            if rc != 0:
                log(f"[prices] ⚠ indicator recompute FAILED (exit {rc}) — prices are "
                    "adjusted but indicators still carry the phantom move")

    return {"ok": classified["ok"] and (not forced or bool(reindexed)),
            "attempted": len(ordered),
            "corporate_actions": [{"symbol": a["symbol"], "exDate": a.get("exDate"),
                                   "subject": a.get("subject")} for a in actions],
            "force_refreshed": forced,
            "reindexed": reindexed,
            "unexplained_price_jumps": unexplained,
            "permanent_failures": sorted(failed),
            "failure_reasons": failed,
            "blocked_by_mapping": classified["blocked_by_mapping"],
            "source_gaps": classified["source_gaps"]}


def stage_prices(universes: list[str]) -> dict:
    return asyncio.run(_prices(universes))


def stage_recon() -> dict:
    import requests
    from pymongo import MongoClient
    db = MongoClient(MONGO_URI)[DB_NAME]

    # Derived union universes (NIFTY500MICRO750, NIFTYSMALLMICRO500) are rebuilt from
    # their parents on every refresh, so they never drift out of date. Each build is
    # replay-validated against the live union before it is written; a mismatch refuses
    # to store rather than corrupting a point-in-time timeline silently.
    from scripts.materialize_broad_universe import materialize_all
    log("[recon] rebuilding derived union universes")
    derived = materialize_all(db=db, store=True, log=lambda m: log(f"[recon]{m}"))
    if not all(derived.values()):
        log(f"[recon] ⚠ derived universe(s) FAILED validation: "
            f"{[k for k, v in derived.items() if not v]} — they keep their previous "
            "membership rather than being overwritten with a wrong timeline")

    # 1) consistency vs freshly pulled constituents.
    #    GATE on the most-recent event only (detects unprocessed recent churn);
    #    the full cumulative replay is reported as info — it also surfaces known
    #    HISTORICAL debt (pre-rename symbols, missing 2024-25 specials) that
    #    shouldn't fail every future run.
    #    A future-dated (announced, not yet effective) event is excluded from the
    #    comparison by check_recon_consistency — its source_url still counts as
    #    captured, which is what dedups the PR scan below.
    today = dt.date.today()
    gate_mismatches, historical_gaps = {}, {}
    newest_event = dt.date(2000, 1, 1)
    stored_urls: set[str] = set()
    for ridx, midx in TRACKED_RECON.items():
        current = {d["Symbol"] for d in db["index_meta"].find({"index_name": midx}, {"Symbol": 1})}
        events = list(db["index_reconstitution"].find({"index_name": ridx}))
        stored_urls.update(ev["source_url"] for ev in events if ev.get("source_url"))
        res = check_recon_consistency(events, current, today)
        if res["newest_effective"]:
            newest_event = max(newest_event, res["newest_effective"])
        if res["gate_mismatch"]:
            gate_mismatches[ridx] = res["gate_mismatch"]
        if res["historical_gap"]:
            historical_gaps[ridx] = res["historical_gap"]

    # 2) uncaptured press releases since ~40d before the newest stored event
    #    (announcements precede effective dates; capture/review dedup via source_url
    #    and the index_recon_pr_log reviewed-no-impact markers)
    reviewed = {d["_id"] for d in db["index_recon_pr_log"].find({}, {"_id": 1})}
    pending = []
    html, fetch_err = None, None
    for attempt in (1, 2):  # niftyindices is slow/flaky; one retry with a long timeout
        try:
            html = requests.get(PRESS_RELEASE_PAGE, headers={"User-Agent": UA}, timeout=90).text
            break
        except Exception as e:
            fetch_err = e
    if html is None:
        return {"ok": False, "error": f"press-release page fetch failed: {fetch_err}",
                "gate_mismatches": gate_mismatches}
    candidates = relevant_prs(parse_pr_page(html), newest_event - dt.timedelta(days=40))
    pending = uncaptured_prs(candidates, stored_urls, reviewed)

    ok = not gate_mismatches and not pending
    if pending:
        log("[recon] UNCAPTURED press releases (run the capture procedure — "
            "docs/capture_index_rebalance.md — then either load events or mark reviewed "
            "via --mark-pr-reviewed):")
        for d, link, title in pending:
            log(f"  {d}  https://www.niftyindices.com/{link}  :: {title}")
    if gate_mismatches:
        log(f"[recon] LAST-EVENT mismatches vs current constituents: {json.dumps(gate_mismatches)}")
    if historical_gaps:
        log(f"[recon] info: historical replay gaps (renames / missing old specials), "
            f"{sum(len(v['removed_but_present']) + len(v['added_but_absent']) for v in historical_gaps.values())} symbols across "
            f"{len(historical_gaps)} indexes — see summary json")
    return {"ok": ok, "newest_event": newest_event.isoformat(),
            "uncaptured_prs": [[str(d), link, t] for d, link, t in pending],
            "gate_mismatches": gate_mismatches, "historical_gaps": historical_gaps}


def stage_indicators() -> dict:
    rc = run_step([PY, "scripts/compute_indicators.py",
                   "--start-fetch", "2016-01-01", "--store-from", "2018-01-01", "--force"])
    return {"ok": rc == 0, "exit": rc}


def stage_report(stale_tolerance: int) -> dict:
    from pymongo import MongoClient
    from market_calendar import load_holidays
    db = MongoClient(MONGO_URI)[DB_NAME]
    expected = most_recent_trading_day(dt.date.today(), load_holidays(db))
    out = {}
    for name, coll, match in [("prices", "prices_2025_2029", {}),
                              ("indicators", "indicators", {"indicator_type": "truevx"})]:
        pipe = ([{"$match": match}] if match else []) + \
               [{"$group": {"_id": "$symbol", "last": {"$max": "$date"}}}]
        last = {r["_id"]: r["last"].date() for r in db[coll].aggregate(pipe, allowDiskUse=True)}
        out[name] = freshness_summary(last, expected, stale_tolerance)
        log(f"[report] {name}: {out[name]['total']} symbols, "
            f"{out[name]['stale_count']} stale (expected {expected}) "
            f"-> {'OK' if out[name]['ok'] else 'STALE'}")
        for d, n in out[name]["histogram_top"]:
            log(f"    {d}: {n}")
    out["ok"] = all(out[k]["ok"] for k in ("prices", "indicators"))
    return out


# --------------------------------------------------------------------------- main
def main() -> int:
    p = argparse.ArgumentParser(description="market-hunt one-command data refresh")
    p.add_argument("--only", help=f"comma-separated subset of {STAGES}")
    p.add_argument("--skip", help="comma-separated stages to skip")
    p.add_argument("--universes", default=",".join(DEFAULT_UNIVERSES),
                   help="index_meta universes whose constituents join the price download set")
    p.add_argument("--stale-tolerance", type=int, default=60,
                   help="max stale symbols tolerated in report (delisted/unmappable names)")
    p.add_argument("--mark-pr-reviewed", metavar="PDF_NAME",
                   help="log a press release (e.g. ind_prs10062026.pdf) as reviewed with "
                        "no tracked-index impact, then exit")
    p.add_argument("--note", default="", help="reviewer note for --mark-pr-reviewed")
    a = p.parse_args()

    if a.mark_pr_reviewed:
        from pymongo import MongoClient
        MongoClient(MONGO_URI)[DB_NAME]["index_recon_pr_log"].replace_one(
            {"_id": a.mark_pr_reviewed},
            {"_id": a.mark_pr_reviewed, "verdict": "no_tracked_impact",
             "note": a.note, "noted_at": dt.datetime.now().isoformat()},
            upsert=True)
        log(f"marked reviewed (no tracked-index impact): {a.mark_pr_reviewed}")
        return 0

    stages = pick_stages(a.only, a.skip)
    universes = [u.strip() for u in a.universes.split(",") if u.strip()]
    log(f"refresh_all: stages={stages}")
    results: dict[str, dict] = {}
    for stage in stages:
        log(f"\n===== stage: {stage} =====")
        try:
            if stage == "calendar":
                results[stage] = stage_calendar()
            elif stage == "urls":
                results[stage] = stage_urls()
            elif stage == "mappings":
                results[stage] = stage_mappings()
            elif stage == "prices":
                results[stage] = stage_prices(universes)
            elif stage == "recon":
                results[stage] = stage_recon()
            elif stage == "indicators":
                results[stage] = stage_indicators()
            elif stage == "report":
                results[stage] = stage_report(a.stale_tolerance)
        except Exception as e:  # a stage crash must not silently kill later stages
            results[stage] = {"ok": False, "error": repr(e)}
        log(f"[{stage}] {'OK' if results[stage].get('ok') else 'PROBLEM'}")

    log("\n===== summary =====")
    bad = [s for s, r in results.items() if not r.get("ok")]
    for s in stages:
        r = results.get(s, {})
        log(f"  {'✓' if r.get('ok') else '⚠'} {s}"
            + (f"  {r.get('error')}" if r.get("error") else ""))
    summary_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                 "refresh_all_last_run.json")
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump({"ran_at": dt.datetime.now().isoformat(), "stages": stages,
                   "results": results}, fh, indent=2, default=str)
    log(f"summary written: {summary_path}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
