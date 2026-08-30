#!/usr/bin/env python3
"""
url_loader.py — Market Hunt index/ETF URL loader & refresher.

Step-1 of the data pipeline: populate the `index_meta_csv_urls` (URL configs) and
`index_meta` (constituents) MongoDB collections via the running FastAPI backend
(POST /api/urls, POST /api/process). Use this for first-time load AND for the
recurring rebalance refresh (NSE rebalances ~monthly/quarterly).

Subcommands
-----------
  discover     Resolve every index page in ALL_URLS -> its direct constituent
               CSV link, save each as a URL config (idempotent), then process.
  add          Add ONE url (remote http(s) or a local repo file) and process it.
  reload       Re-download & replace data for existing URL configs (rebalance).
  load-local   Serve repo root over http and load a local CSV (e.g. market_indexes.csv,
               etfs.csv) as a universe.
  build-etf    (Re)build etfs.csv in repo root from nseindia.com/api/etf.
  list         List saved URL configs.

Examples
--------
  py = .venv\\Scripts\\python   (run from the market-hunt repo root)

  # First-time full load of all nifty index constituents:
  %py% scripts/url_loader.py discover --process

  # Monthly rebalance refresh (re-pull every saved URL's latest constituents):
  %py% scripts/url_loader.py reload --all

  # Add a single new index URL (index name auto-extracted if omitted):
  %py% scripts/url_loader.py add "https://niftyindices.com/IndexConstituent/ind_niftyXXXlist.csv"

  # Add a single niftyindices *page* (the CSV link is auto-resolved):
  %py% scripts/url_loader.py add "https://niftyindices.com/indices/equity/.../some-index" --resolve

  # Load the local benchmark + ETF seed CSVs:
  %py% scripts/url_loader.py load-local market_indexes.csv --index-name "MARKET INDEXES"
  %py% scripts/url_loader.py build-etf
  %py% scripts/url_loader.py load-local etfs.csv --index-name ETF
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin

import requests

# --- config -----------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent          # market-hunt repo root
ALL_URLS = ROOT / "ALL_URLS"
API = os.environ.get("MH_API", "http://localhost:3001")
LOCAL_PORT = int(os.environ.get("MH_LOCAL_PORT", "8000"))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
CSV_HREF = re.compile(r'href="([^"]*IndexConstituent/[^"]+\.csv)"', re.IGNORECASE)


def log(msg: str) -> None:
    print(msg, flush=True)


# --- backend helpers --------------------------------------------------------
def api_get_urls() -> list[dict]:
    r = requests.get(f"{API}/api/urls", timeout=30)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = data.get("urls") or data.get("data") or []
    return data


def api_add_url(url: str, index_name: str = "", description: str = "",
                tags: list[str] | None = None, is_active: bool = True) -> tuple[bool, str]:
    body = {"url": url, "index_name": index_name, "description": description,
            "tags": tags or [], "is_active": is_active}
    r = requests.post(f"{API}/api/urls", json=body, timeout=30)
    if r.status_code == 200 and r.json().get("success"):
        return True, r.json().get("message", "")
    # 400 => commonly "already exists"; treat as non-fatal for idempotency
    try:
        detail = r.json().get("detail") or r.text
    except Exception:
        detail = r.text
    return False, f"[{r.status_code}] {detail}"


def api_process(url_ids: list[str]) -> dict:
    r = requests.post(f"{API}/api/process", json={"url_ids": url_ids}, timeout=900)
    r.raise_for_status()
    return r.json()


def ids_for(urls: list[str] | None = None, active_only: bool = False) -> list[str]:
    """Resolve url-config ids. If `urls` given, match those; else all (optionally active)."""
    configs = api_get_urls()
    out = []
    for c in configs:
        if active_only and not c.get("is_active", True):
            continue
        if urls is not None and c.get("url") not in urls:
            continue
        cid = c.get("_id") or c.get("id")
        if cid:
            out.append(cid)
    return out


def report_process(res: dict) -> list[dict]:
    """Log a /api/process response; return the failed result rows."""
    log(f"  -> {res.get('message')}")
    bad = [r for r in res.get("results", []) if not r.get("success")]
    ok = [r for r in res.get("results", []) if r.get("success")]
    for r in ok:
        log(f"     OK   {r.get('index_name','?'):<32} docs={r.get('documents_loaded','?')}")
    for r in bad:
        log(f"     FAIL {r.get('index_name','?'):<32} {str(r.get('error') or r.get('message'))[:70]}")
    return bad


# --- local http server (for local-file URLs like localhost:8000/etfs.csv) ---
def _port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


class _LocalServer:
    """Serve the repo root on a fixed port so local CSV URLs resolve. No-op if already up."""
    def __init__(self, root: Path, port: int):
        self.root, self.port, self.httpd = root, port, None

    def __enter__(self):
        if _port_open("127.0.0.1", self.port):
            log(f"[server] reusing existing http server on :{self.port}")
            return self
        handler = partial(SimpleHTTPRequestHandler, directory=str(self.root))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        for _ in range(20):
            if _port_open("127.0.0.1", self.port):
                break
            time.sleep(0.1)
        log(f"[server] serving {self.root} on http://localhost:{self.port}")
        return self

    def __exit__(self, *exc):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            log("[server] stopped")


# --- page -> csv resolution -------------------------------------------------
def resolve_csv(page_url: str) -> str | None:
    r = requests.get(page_url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    m = CSV_HREF.search(r.text)
    return urljoin(page_url, m.group(1)) if m else None


# --- subcommands ------------------------------------------------------------
def cmd_discover(args) -> int:
    if not ALL_URLS.exists():
        log(f"ERROR: {ALL_URLS} not found")
        return 1
    pages = [ln.strip() for ln in ALL_URLS.read_text().splitlines() if ln.strip()]
    log(f"Resolving {len(pages)} index pages from ALL_URLS ...")
    resolved, unresolved = {}, []
    for i, page in enumerate(pages, 1):
        slug = page.rstrip("/").split("/")[-1]
        try:
            csv_url = resolve_csv(page)
            if csv_url:
                resolved[csv_url] = slug
                log(f"[{i:>2}/{len(pages)}] {slug:<46} -> {csv_url.split('/')[-1]}")
            else:
                unresolved.append((slug, "no csv link"))
                log(f"[{i:>2}/{len(pages)}] {slug:<46} -> NO CSV LINK")
        except Exception as e:
            unresolved.append((slug, str(e)[:60]))
            log(f"[{i:>2}/{len(pages)}] {slug:<46} -> ERROR {str(e)[:40]}")

    saved = exists = failed = 0
    for csv_url, slug in resolved.items():
        ok, msg = api_add_url(csv_url, index_name="",
                              description=f"Nifty index constituents ({slug})",
                              tags=["niftyindices", "constituents"])
        if ok:
            saved += 1
        elif "exist" in msg.lower():
            exists += 1
        else:
            failed += 1
            log(f"  SAVE FAIL {csv_url.split('/')[-1]}: {msg}")
    log(f"\nresolved={len(resolved)} saved_new={saved} already_present={exists} "
        f"save_failed={failed} page_unresolved={len(unresolved)}")
    if unresolved:
        log("unresolved pages: " + ", ".join(s for s, _ in unresolved))

    if args.process:
        log("\nProcessing all niftyindices constituent URLs ...")
        report_process(api_process(ids_for(list(resolved.keys()))))
    return 0


def cmd_add(args) -> int:
    url = args.url
    if args.resolve and "IndexConstituent" not in url:
        log(f"Resolving CSV link from page: {url}")
        csv_url = resolve_csv(url)
        if not csv_url:
            log("ERROR: no constituent CSV link found on that page")
            return 1
        url = csv_url
        log(f"  -> {url}")
    ok, msg = api_add_url(url, index_name=args.index_name or "",
                          description=args.description or "Added via url_loader",
                          tags=(args.tags.split(",") if args.tags else ["manual"]))
    log(f"add: {'OK' if ok else 'SKIP'} {msg}")
    if not ok and "exist" not in msg.lower():
        return 1
    if args.process:
        report_process(api_process(ids_for([url])))
    return 0


def cmd_reload(args) -> int:
    if args.ids:
        ids = args.ids
    elif args.urls:
        ids = ids_for(args.urls)
    else:  # --all
        ids = ids_for(active_only=args.active_only)
    if not ids:
        log("No matching URL configs to reload.")
        return 1
    log(f"Reloading {len(ids)} URL config(s) (re-download + replace per index) ...")
    # Process in chunks: one giant POST holds the connection open for minutes and a
    # single slow niftyindices fetch can reset it, losing the whole batch. Failed
    # url_ids (per-URL failures AND whole-chunk transport errors) get one retry
    # pass; niftyindices throttling is transient, a pause + retry recovers most.
    CHUNK = 15

    def process_ids(id_list: list[str], tag: str) -> list[str]:
        failed: list[str] = []
        for i in range(0, len(id_list), CHUNK):
            chunk = id_list[i:i + CHUNK]
            try:
                bad = report_process(api_process(chunk))
                failed += [r["url_id"] for r in bad if r.get("url_id")]
            except Exception as e:
                log(f"  [{tag}] chunk {i // CHUNK + 1} transport error: {str(e)[:80]}")
                failed += chunk
            # fast-abort: the whole first chunk failing means the source is
            # blocking/down — grinding through the rest just burns timeouts.
            if i == 0 and len(failed) == len(chunk):
                log(f"  [{tag}] entire first chunk failed — source blocked/down, aborting pass")
                return failed + id_list[i + CHUNK:]
        return failed

    # local-file URLs (localhost) need the root server up during processing
    with _LocalServer(ROOT, LOCAL_PORT):
        failed = process_ids(ids, "pass1")
        if failed:
            log(f"Retrying {len(failed)} failed URL(s) after a 30s pause ...")
            time.sleep(30)
            failed = process_ids(failed, "retry")
    if failed:
        log(f"reload finished with {len(failed)} URL(s) still failing "
            "(niftyindices likely throttling — re-run the urls stage later; "
            "constituents keep their previous rows until a reload succeeds)")
    return 1 if failed else 0


def cmd_load_local(args) -> int:
    path = (ROOT / args.file).resolve()
    if not path.exists():
        log(f"ERROR: {path} not found")
        return 1
    rel = path.relative_to(ROOT).as_posix()
    url = f"http://localhost:{LOCAL_PORT}/{rel}"
    name = args.index_name or path.stem.upper().replace("_", " ")
    with _LocalServer(ROOT, LOCAL_PORT):
        ok, msg = api_add_url(url, index_name=name,
                              description=args.description or f"Local seed: {rel}",
                              tags=["local", "seed"])
        log(f"add: {'OK' if ok else 'SKIP'} {msg}")
        report_process(api_process(ids_for([url])))
    return 0


def cmd_build_etf(args) -> int:
    out = ROOT / "etfs.csv"
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                      "Accept-Language": "en-US,en;q=0.9",
                      "Referer": "https://www.nseindia.com/market-data/exchange-traded-funds-etf"})
    s.get("https://www.nseindia.com", timeout=30)
    s.get("https://www.nseindia.com/market-data/exchange-traded-funds-etf", timeout=30)
    rows = s.get("https://www.nseindia.com/api/etf", timeout=30).json().get("data", [])
    log(f"fetched {len(rows)} ETFs from NSE")
    import csv as _csv
    seen, out_rows = set(), []
    for it in rows:
        sym = (it.get("symbol") or "").strip()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out_rows.append({"Company Name": sym, "Industry": "ETF", "Symbol": sym,
                         "Series": (it.get("series") or "EQ").strip(), "ISIN Code": ""})
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=["Company Name", "Industry", "Symbol", "Series", "ISIN Code"])
        w.writeheader()
        w.writerows(out_rows)
    log(f"wrote {len(out_rows)} unique ETF rows -> {out}")
    if args.load:
        return cmd_load_local(argparse.Namespace(
            file="etfs.csv", index_name="ETF", description="NSE ETFs (built from api/etf)"))
    return 0


def cmd_list(args) -> int:
    configs = api_get_urls()
    log(f"{len(configs)} URL config(s):")
    for c in sorted(configs, key=lambda x: x.get("index_name", "")):
        flag = "" if c.get("is_active", True) else " (inactive)"
        log(f"  {c.get('index_name','?'):<34} {c.get('url','')}{flag}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Market Hunt URL loader / refresher")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="resolve ALL_URLS pages -> CSVs, save all")
    d.add_argument("--process", action="store_true", help="also download data after saving")
    d.set_defaults(func=cmd_discover)

    a = sub.add_parser("add", help="add one URL")
    a.add_argument("url")
    a.add_argument("--index-name", default="")
    a.add_argument("--description", default="")
    a.add_argument("--tags", default="")
    a.add_argument("--resolve", action="store_true", help="treat url as a page; resolve its CSV link")
    a.add_argument("--no-process", dest="process", action="store_false")
    a.set_defaults(process=True, func=cmd_add)

    r = sub.add_parser("reload", help="re-download existing configs (rebalance refresh)")
    g = r.add_mutually_exclusive_group()
    g.add_argument("--all", action="store_true")
    g.add_argument("--urls", nargs="+")
    g.add_argument("--ids", nargs="+")
    r.add_argument("--active-only", action="store_true")
    r.set_defaults(func=cmd_reload)

    ll = sub.add_parser("load-local", help="serve root + load a local CSV")
    ll.add_argument("file")
    ll.add_argument("--index-name", default="")
    ll.add_argument("--description", default="")
    ll.set_defaults(func=cmd_load_local)

    be = sub.add_parser("build-etf", help="(re)build etfs.csv from nseindia.com/api/etf")
    be.add_argument("--load", action="store_true", help="also load it as the ETF universe")
    be.set_defaults(func=cmd_build_etf)

    li = sub.add_parser("list", help="list saved URL configs")
    li.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except requests.exceptions.ConnectionError:
        log(f"ERROR: cannot reach backend at {API}. Is uvicorn api_server:app --port 3001 running?")
        return 2


if __name__ == "__main__":
    sys.exit(main())
