"""Capture NIFTY Microcap 250 reconstitution from niftyindices PRs.

Reuses the (effective_date, source_url) review pairs already in market_hunt.index_reconstitution
(the PRs are shared across indices), fetches each PR via urllib, parses the 'NIFTY Microcap 250'
section's included(added)/excluded(removed) symbol tables. VALIDATION MODE by default (parse +
print). Pass --store to upsert into index_reconstitution (index_name='NIFTYMICROCAP250').
"""
import sys, re, urllib.request
from io import BytesIO
from datetime import datetime
from pypdf import PdfReader
from pymongo import MongoClient

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
STORE = "--store" in sys.argv
db = MongoClient("mongodb://localhost:27017/")["market_hunt"]


def text_of(url):
    data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45).read()
    return "\n".join((p.extract_text() or "") for p in PdfReader(BytesIO(data)).pages)


def parse_block(block):
    """symbols from a numbered 'N  Company Name  SYMBOL' table block."""
    syms = []
    for line in block.splitlines():
        m = re.match(r"\s*\d+\s+.+?\s+([A-Z][A-Z0-9&.\-]{1,})\*?\s*$", line)
        if m:
            s = m.group(1).rstrip(".*").strip()
            if s and s not in ("LTD", "NO", "SYMBOL") and len(s) >= 2:
                syms.append(s)
    return syms


def parse_microcap(txt):
    low = txt.lower()
    h = re.search(r"nifty\s+microcap\s+250", low)
    if not h:
        return None, None
    start = h.end()
    # section ends at the next "NIFTY <Index>" heading after a section number/letter
    nxt = re.search(r"\n\s*(?:\d+\)|[a-z]\))\s*(?:NIFTY|Nifty)\s", txt[start:])
    section = txt[start: start + (nxt.start() if nxt else 8000)]
    inc = re.search(r"being\s+included", section, re.I)
    exc = re.search(r"being\s+excluded", section, re.I)
    added = removed = []
    if inc and exc:
        if inc.start() < exc.start():
            added = parse_block(section[inc.end():exc.start()])
            removed = parse_block(section[exc.end():])
        else:
            removed = parse_block(section[exc.end():inc.start()])
            added = parse_block(section[inc.end():])
    elif exc:
        removed = parse_block(section[exc.end():])
    elif inc:
        added = parse_block(section[inc.end():])
    return added, removed


# review pairs already known (shared PRs); dedupe by url, keep an effective_date
pairs = {}
for ev in db.index_reconstitution.find({}, {"effective_date": 1, "source_url": 1}):
    u = ev.get("source_url")
    if u and u not in pairs:
        pairs[u] = ev["effective_date"]
rows = sorted(pairs.items(), key=lambda kv: kv[1])

print(f"scanning {len(rows)} known review PRs for a Microcap250 section...\n")
events = []
for url, eff in rows:
    try:
        added, removed = parse_microcap(text_of(url))
    except Exception as e:
        print(f"{str(eff)[:10]}  {url.split('/')[-1]:28} ERROR {type(e).__name__}: {str(e)[:60]}")
        continue
    if added is None:
        print(f"{str(eff)[:10]}  {url.split('/')[-1]:28} no Microcap250 section")
        continue
    print(f"{str(eff)[:10]}  {url.split('/')[-1]:28} +{len(added)} added / -{len(removed)} removed")
    print(f"     added:   {', '.join(added) if added else '-'}")
    print(f"     removed: {', '.join(removed) if removed else '-'}")
    events.append((eff, url, added, removed))

if STORE:
    for eff, url, added, removed in events:
        eid = f"NIFTYMICROCAP250_{str(eff)[:10]}"
        db.index_reconstitution.update_one({"_id": eid}, {"$set": {
            "_id": eid, "index_name": "NIFTYMICROCAP250",
            "effective_date": eff if isinstance(eff, datetime) else datetime.strptime(str(eff)[:10], "%Y-%m-%d"),
            "added": added, "removed": removed, "source_url": url,
            "source": "niftyindices_press_release", "verified": True,
            "loaded_at": datetime(2026, 6, 21)}}, upsert=True)
    print(f"\nSTORED {len(events)} Microcap250 events into index_reconstitution.")
else:
    print(f"\n[validation only — {len(events)} reviews parsed. Re-run with --store to upsert.]")
