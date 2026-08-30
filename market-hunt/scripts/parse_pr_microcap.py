"""Fetch niftyindices press-release PDFs (via urllib — WebFetch times out) and locate the
NIFTY Microcap 250 reconstitution (added/removed) section. Maps which PRs cover Microcap250
and dumps the surrounding text so we can build the reconstitution capture.
"""
import sys, re, urllib.request
from io import BytesIO
from pypdf import PdfReader

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
# verified-good PR URLs already in Mongo (announcement-date filenames), spanning 2020-2026
URLS = [
    "https://www.niftyindices.com/Press_Release/ind_prs23082021.pdf",   # ~Sep-2021 review
    "https://www.niftyindices.com/Press_Release/ind_prs24022022_1.pdf", # ~Mar-2022
    "https://www.niftyindices.com/Press_Release/ind_prs23082024.pdf",   # ~Sep-2024
    "https://www.niftyindices.com/Press_Release/ind_prs22082025.pdf",   # ~Sep-2025
]
if len(sys.argv) > 1:
    URLS = sys.argv[1:]


def text_of(url):
    data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=45).read()
    reader = PdfReader(BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


for url in URLS:
    print("\n" + "=" * 90)
    print(url)
    try:
        txt = text_of(url)
    except Exception as e:
        print("  fetch/parse error:", type(e).__name__, str(e)[:150]); continue
    print(f"  chars={len(txt)}")
    low = txt.lower()
    for needle in ("microcap 250", "micro cap 250", "microcap250", "nifty microcap"):
        idx = low.find(needle)
        if idx != -1:
            print(f"  >>> found '{needle}' at {idx}; surrounding 1200 chars:")
            print(txt[max(0, idx - 200): idx + 1000])
            break
    else:
        # list the index headings present so we see what this PR covers
        heads = sorted(set(re.findall(r"NIFTY[ A-Za-z0-9]{2,30}", txt)))[:40]
        print("  (no Microcap250 section) index mentions:", heads[:25])
