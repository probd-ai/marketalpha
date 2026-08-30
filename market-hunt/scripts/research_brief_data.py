#!/usr/bin/env python3
"""Gather every input a research brief needs, in one pass. Analysis is NOT done here.

This script only collects and prints raw material — book, prices, strength scores,
macro/sector/per-stock news, exchange filings. The judgement (verdict, severity,
contradictions) is the model's job in the `research-analyst` skill, because that is
the part that must not be hard-coded.

  python scripts/research_brief_data.py --portfolio portfolio_1782072903878
  python scripts/research_brief_data.py --symbols PAYTM,LAURUSLABS,RADICO
  python scripts/research_brief_data.py --portfolio <id> --days 60 --no-news

Everything it prints is timestamped and attributed so the brief can cite sources.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402
from pymongo import MongoClient  # noqa: E402

import settings  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
API = "http://127.0.0.1:3001"
GNEWS = "https://news.google.com/rss/search"
ET_MARKETS = "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"
BSE_ANN = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"

# Google News matches on company NAME, not ticker. `symbol_mappings.company_name` is
# empty in this DB — `nse_name` carries the real name — and even that is often the
# legal name ("ONE 97 COMMUNICATIONS LTD") which returns nothing useful. Overrides
# below are the names the press actually uses. Add to this as the book changes.
NAME_OVERRIDES = {
    "PAYTM": "Paytm", "M&MFIN": "Mahindra Finance", "BAJAJ-AUTO": "Bajaj Auto",
    "NYKAA": "Nykaa", "ADANIENSOL": "Adani Energy Solutions",
    "PHOENIXLTD": "Phoenix Mills", "RADICO": "Radico Khaitan",
    "DIVISLAB": "Divi's Laboratories", "TORNTPHARM": "Torrent Pharmaceuticals",
    "TVSMOTOR": "TVS Motor", "LAURUSLABS": "Laurus Labs",
    "FEDERALBNK": "Federal Bank", "IDFCFIRSTB": "IDFC First Bank",
    "APARINDS": "Apar Industries", "EXIDEIND": "Exide Industries",
    "FLUOROCHEM": "Gujarat Fluorochemicals", "AJANTPHARM": "Ajanta Pharma",
    "SONACOMS": "Sona BLW", "SANSERA": "Sansera Engineering",
    "SHRIRAMFIN": "Shriram Finance", "TITAN": "Titan Company",
    "EICHERMOT": "Eicher Motors", "MOTHERSON": "Samvardhana Motherson",
    "HEROMOTOCO": "Hero MotoCorp",
}


def _feed(url: str, n: int = 8) -> list[tuple[str, str, str]]:
    """(date, source, headline) from an RSS feed. Handles CDATA-wrapped titles."""
    try:
        r = requests.get(url, headers=UA, timeout=25)
    except Exception as e:
        return [("ERR", "-", str(e)[:70])]
    out = []
    for it in re.findall(r"<item>(.*?)</item>", r.text, re.S)[:n]:
        t = re.search(r"<title>(.*?)</title>", it, re.S)
        d = re.search(r"<pubDate>(.*?)</pubDate>", it, re.S)
        s = re.search(r"<source[^>]*>(.*?)</source>", it, re.S)
        if t:
            # ET wraps titles in CDATA; a non-greedy optional group leaves the ]]>
            # behind, so strip the wrapper explicitly rather than in the pattern.
            title = t.group(1).replace("<![CDATA[", "").replace("]]>", "")
            out.append((d.group(1)[5:16] if d else "?",
                        html.unescape(s.group(1))[:22] if s else "-",
                        html.unescape(re.sub(r"</?[a-zA-Z][^>]*>", "", title)).strip()[:120]))
    return out


def gnews(query: str, days: int, n: int = 8):
    q = urllib.parse.quote(f'{query} when:{days}d')
    return _feed(f"{GNEWS}?q={q}&hl=en-IN&gl=IN&ceid=IN:en", n)


def bse_filings(days: int, categories=("Result", "Board Meeting", "Company Update"),
                per_cat: int = 40):
    """Recent exchange filings. NOTE: strCat=-1 returns ZERO rows — a category is
    mandatory. Requires a browser UA plus a bseindia Referer/Origin or it 403s."""
    h = dict(UA)
    h.update({"Accept": "application/json", "Referer": "https://www.bseindia.com/",
              "Origin": "https://www.bseindia.com"})
    to = datetime.now().strftime("%Y%m%d")
    frm = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    out = []
    for cat in categories:
        url = (f"{BSE_ANN}?pageno=1&strCat={urllib.parse.quote(cat)}&strPrevDate={frm}"
               f"&strScrip=&strSearch=P&strToDate={to}&strType=C&subcategory=")
        try:
            rows = (requests.get(url, headers=h, timeout=30).json() or {}).get("Table") or []
        except Exception as e:
            out.append({"category": cat, "error": str(e)[:70]})
            continue
        for x in rows[:per_cat]:
            out.append({"category": cat, "scrip_cd": x.get("SCRIP_CD"),
                        "dt_tm": str(x.get("DT_TM"))[:16], "critical": x.get("CRITICALNEWS"),
                        "headline": (x.get("HEADLINE") or x.get("NEWSSUB") or "")[:120]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--portfolio", help="portfolio id — uses its cached current book")
    ap.add_argument("--symbols", help="comma-separated symbols instead of a portfolio")
    ap.add_argument("--days", type=int, default=45, help="news lookback (default 45)")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--no-filings", action="store_true")
    args = ap.parse_args()

    db = MongoClient(settings.MONGO_URI)[settings.DB_NAME]
    book, meta = [], {}

    if args.portfolio:
        p = requests.get(f"{API}/api/portfolio/{args.portfolio}", timeout=60).json()["portfolio"]
        lr = requests.get(f"{API}/api/portfolio/{args.portfolio}/last-run", timeout=180).json()
        res = lr.get("result")
        if not res:
            print("NO CACHED RUN — open the portfolio page and press Run first.")
            return 1
        meta = {"name": p["name"], "capital": p["total_capital"],
                "inception": p["inception_date"], "as_of": res["portfolio"]["as_of"],
                "computed_at": (res.get("computed_at") or "")[:16],
                "config_changed": lr.get("config_changed"), "data_stale": lr.get("data_stale"),
                "summary": res["summary"],
                "sleeves": [{k: s.get(k) for k in
                             ("label", "weight", "universe", "max_holdings",
                              "momentum_ranking", "rebalance_frequency", "rebalance_date")}
                            for s in p["sleeves"]]}
        book = sorted(res["current_book"], key=lambda x: -x["market_value"])
    elif args.symbols:
        book = [{"symbol": s.strip().upper(), "weight": None, "pnl_percent": None,
                 "market_value": None, "sleeve": ""} for s in args.symbols.split(",")]
    else:
        ap.error("pass --portfolio or --symbols")

    syms = [h["symbol"] for h in book]
    print("=" * 100)
    print("RESEARCH BRIEF — RAW INPUTS")
    print(f"generated {datetime.now().isoformat(timespec='seconds')}")
    if meta:
        s = meta["summary"]
        print(f"\nPORTFOLIO {meta['name']} · capital {meta['capital']:,.0f} · "
              f"inception {meta['inception']} · as_of {meta['as_of']}")
        print(f"  value {s['total_value']:,.0f} · return {s['total_return']}% · "
              f"benchmark {s['benchmark_return']}% · alpha {s['alpha']}% · DD {s['max_drawdown']}%")
        print(f"  cached {meta['computed_at']} · config_changed={meta['config_changed']} "
              f"· data_stale={meta['data_stale']}")
        for sl in meta["sleeves"]:
            print(f"  sleeve {sl['label']}: w={sl['weight']}% {sl['universe']} "
                  f"top{sl['max_holdings']} by {sl['momentum_ranking']} "
                  f"{sl['rebalance_frequency']}/{sl['rebalance_date']}")

    # ── holdings + price + strength, the quantitative side of every claim ──────
    print("\n" + "=" * 100)
    print("BOOK — price action and strength (fact-check news against THIS)")
    print(f"{'symbol':13s} {'wt%':>6s} {'pnl%':>7s} {'last':>9s} {'1w':>7s} {'1m':>7s} "
          f"{'3m':>7s} {'12m':>8s} {'vol':>5s} {'RSZ':>5s} {'algn':>4s}")
    since = datetime.now() - timedelta(days=500)
    for h in book:
        s = h["symbol"]
        px = {}
        for c in db.list_collection_names():
            if not c.startswith("prices_"):
                continue
            for d in db[c].find({"symbol": s, "date": {"$gte": since}},
                                {"date": 1, "close_price": 1}):
                px[d["date"].strftime("%Y-%m-%d")] = float(d["close_price"])
        cl = [v for _, v in sorted(px.items())]
        r = db.rsz.find_one({"symbol": s}, sort=[("date", -1)]) or {}
        f = lambda n: (cl[-1] / cl[-n] - 1) * 100 if len(cl) > n else float("nan")
        wt = f"{h['weight']:.1f}" if h.get("weight") is not None else "-"
        pl = f"{h['pnl_percent']:.1f}" if h.get("pnl_percent") is not None else "-"
        print(f"{s:13s} {wt:>6s} {pl:>7s} {cl[-1] if cl else 0:>9.1f} {f(5):>+6.1f}% "
              f"{f(22):>+6.1f}% {f(66):>+6.1f}% {f(252):>+7.1f}% "
              f"{(r.get('vol_pct') or 0):>4.0f}% {(r.get('rsz_score') or 0):>5.1f} "
              f"{r.get('aligned', '-'):>4}")

    for idx in ("Nifty 50", "Nifty 500"):
        px = {}
        for c in db.list_collection_names():
            if not c.startswith("prices_"):
                continue
            for d in db[c].find({"symbol": idx, "date": {"$gte": since}},
                                {"date": 1, "close_price": 1}):
                px[d["date"].strftime("%Y-%m-%d")] = float(d["close_price"])
        cl = [v for _, v in sorted(px.items())]
        if cl:
            f = lambda n: (cl[-1] / cl[-n] - 1) * 100 if len(cl) > n else float("nan")
            print(f"{idx:13s} {'':>6s} {'':>7s} {cl[-1]:>9.0f} {f(5):>+6.1f}% {f(22):>+6.1f}% "
                  f"{f(66):>+6.1f}% {f(252):>+7.1f}%   <- BENCHMARK")

    if args.no_news:
        return 0

    # ── macro -> sector -> stock, in that order ───────────────────────────────
    print("\n" + "=" * 100)
    print("MARKET CONTEXT — India (Economic Times markets feed)")
    for d, _s, t in _feed(ET_MARKETS, 10):
        print(f"  [{d:12s}] {t}")

    print("\nMACRO — global items with a stated India transmission")
    for d, s, t in gnews('(Fed OR "US tariffs" OR "crude oil" OR "FII flows" OR rupee) '
                         'India stock market', 10, 8):
        print(f"  [{d:12s}] {s:22s} {t}")

    sectors = sorted({(db.symbol_mappings.find_one({"symbol": s}) or {}).get("industry", "")
                      for s in syms} - {""})
    print(f"\nSECTORS represented in the book: {sectors or '(industry field is empty in DB — infer from names)'}")
    for sec in (sectors or [])[:6]:
        print(f"  -- {sec}")
        for d, _s, t in gnews(f"Indian {sec} stocks", 14, 3):
            print(f"     [{d:12s}] {t}")

    print("\n" + "=" * 100)
    print(f"PER-STOCK NEWS — last {args.days} days")
    for h in book:
        s = h["symbol"]
        nm = NAME_OVERRIDES.get(s) or (
            db.symbol_mappings.find_one({"symbol": s}) or {}).get("nse_name") or s
        print(f"\n--- {s} ({nm}) ---")
        for d, src, t in gnews(f'"{nm}"', args.days, 7):
            print(f"  [{d:12s}] {src:22s} {t}")
        time.sleep(0.4)          # be polite to the feed

    if not args.no_filings:
        print("\n" + "=" * 100)
        print(f"EXCHANGE FILINGS — BSE, last {args.days}d (authoritative; press claims "
              f"with no filing here are UNCONFIRMED)")
        print("NOTE: keyed by BSE SCRIP_CD. NSE-symbol -> BSE-code mapping is NOT built,")
        print("      so match by headline text for now and say so in the brief.")
        for f in bse_filings(args.days)[:60]:
            if f.get("error"):
                print(f"  [{f['category']}] ERROR {f['error']}")
            else:
                print(f"  [{f['dt_tm']:16s}] scrip={f['scrip_cd']} crit={f['critical']} "
                      f"{f['category'][:14]:15s} {f['headline']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
