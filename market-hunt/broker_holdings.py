"""Parse and reconcile broker holdings exports (Dhan / Kite).

This is the deterministic layer under the AI bookkeeper. The model resolves company
names to tickers and interprets intent; everything here is exact, testable and
reproducible, because these numbers become orders and later feed execution-cost
analysis. A reasoning slip in the arithmetic would be indistinguishable from a real
gap, so the arithmetic does not go near a model.

Two hazards shape the design:

* **Dhan identifies stocks by display name, not ticker.** "Great Eastern Shipping
  Company" has nse_name "THE GE SHPG.LTD" -- no fuzzy matcher resolves that, and a
  wrong guess reconciles the wrong stock. So nothing here guesses: names resolve
  through a persisted alias table or they are reported UNRESOLVED and block the
  reconciliation.
* **Dhan exports carry PII** (holder name, UCC, mobile, email). None of it is
  retained; the account is tracked by a one-way fingerprint so a file can still be
  proved to belong to the mapped account.

Quantities are the only thing reconciled. The broker's LTP is from whenever the user
downloaded the file while our prices come from the last data refresh, so any value
comparison differs for reasons that carry no information.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import Iterable

# Corporate-form and jurisdiction noise that differs between broker display names and
# NSE's `nse_name`, and never distinguishes two companies.
_NOISE_TOKENS = {
    "LTD", "LIMITED", "LTD.", "THE", "INDIA", "INDIAN", "CO", "COMPANY",
    "CORP", "CORPORATION", "PVT", "PRIVATE", "AND", "&",
}
_DHAN_HEADER = "Scrip Name"
_KITE_HEADER = "Instrument"
_DHAN_DOWNLOADED = re.compile(r"downloaded at\s+(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})")


@dataclass(frozen=True)
class Holding:
    raw_name: str                 # exactly as the broker wrote it
    quantity: float
    avg_cost: float | None = None
    ltp: float | None = None      # informational only; never reconciled
    symbol: str | None = None     # None until resolved (always None for Dhan here)


@dataclass(frozen=True)
class BrokerSnapshot:
    broker: str
    holdings: list[Holding] = field(default_factory=list)
    file_as_of: dt.datetime | None = None      # timestamp stated INSIDE the file
    account_fingerprint: str | None = None     # one-way; never the raw identifier
    content_hash: str = ""

    def quantities(self) -> dict[str, float]:
        """{symbol: qty} for resolved rows only. Unresolved rows are excluded on
        purpose -- callers must check `unresolved()` and refuse to reconcile."""
        out: dict[str, float] = {}
        for h in self.holdings:
            if h.symbol:
                out[h.symbol] = out.get(h.symbol, 0.0) + h.quantity
        return out

    def unresolved(self) -> list[str]:
        return [h.raw_name for h in self.holdings if not h.symbol]


def _fingerprint(value: str) -> str:
    """One-way account identifier. Enough to prove a file belongs to the mapped
    account; never enough to recover the UCC."""
    return hashlib.sha256(value.strip().upper().encode()).hexdigest()[:12]


def content_hash(text: str) -> str:
    """Stable hash of the file body, so re-reading an unchanged file is a no-op and
    the snapshot log records only genuine changes."""
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def detect_format(text: str) -> str | None:
    """'dhan' | 'kite' | None. Returns None rather than guessing -- an unknown
    export must fail loudly, not be parsed as the wrong broker."""
    if not text or not text.strip():
        return None
    head = "\n".join(text.splitlines()[:40])
    if _DHAN_HEADER in head and "Avg. Buy Rate" in head:
        return "dhan"
    first = text.lstrip().splitlines()[0] if text.strip() else ""
    if _KITE_HEADER in first and "Qty." in first:
        return "kite"
    return None


def _f(value: str) -> float | None:
    try:
        return float(str(value).replace(",", "").strip().strip('"'))
    except (TypeError, ValueError):
        return None


def parse_dhan(text: str) -> BrokerSnapshot:
    """Holdings between the 'Scrip Name' header and the totals footer.

    PII in the preamble is read only to derive the account fingerprint and is never
    stored. Symbols stay None: Dhan gives display names, which resolve separately.
    """
    lines = text.splitlines()
    fingerprint = None
    for line in lines[:12]:
        if line.upper().startswith("UCC,"):
            ucc = line.split(",", 1)[1].strip().strip('"')
            if ucc:
                fingerprint = _fingerprint(ucc)
            break

    as_of = None
    m = _DHAN_DOWNLOADED.search(text)
    if m:
        d, mo, y, hh, mm = (int(x) for x in m.groups())
        as_of = dt.datetime(y, mo, d, hh, mm)

    start = next((i for i, ln in enumerate(lines) if ln.startswith(_DHAN_HEADER)), None)
    holdings: list[Holding] = []
    if start is not None:
        for row in csv.reader(io.StringIO("\n".join(lines[start + 1:]))):
            if not row or not (row[0] or "").strip():
                break                                   # blank line ends the block
            name = row[0].strip()
            if name.lower().startswith("investment") or name.upper().startswith("NOTE"):
                break                                   # totals footer / trailer
            qty = _f(row[1]) if len(row) > 1 else None
            if qty is None:
                continue
            holdings.append(Holding(raw_name=name, quantity=qty,
                                    avg_cost=_f(row[2]) if len(row) > 2 else None,
                                    ltp=_f(row[4]) if len(row) > 4 else None))
    return BrokerSnapshot(broker="dhan", holdings=holdings, file_as_of=as_of,
                          account_fingerprint=fingerprint, content_hash=content_hash(text))


def parse_kite(text: str) -> BrokerSnapshot:
    """Kite's Instrument column is already an NSE ticker, so rows resolve directly.

    Kite states no download time and no account identity, so provenance is weaker
    than Dhan's: callers fall back to file mtime and must label it as such.
    """
    holdings: list[Holding] = []
    rows = list(csv.reader(io.StringIO(text)))
    for row in rows[1:]:
        if not row or not (row[0] or "").strip():
            continue
        sym = row[0].strip().strip('"')
        qty = _f(row[1]) if len(row) > 1 else None
        if qty is None:
            continue
        holdings.append(Holding(raw_name=sym, symbol=sym, quantity=qty,
                                avg_cost=_f(row[2]) if len(row) > 2 else None,
                                ltp=_f(row[3]) if len(row) > 3 else None))
    return BrokerSnapshot(broker="kite", holdings=holdings, file_as_of=None,
                          account_fingerprint=None, content_hash=content_hash(text))


def parse(text: str) -> BrokerSnapshot:
    fmt = detect_format(text)
    if fmt == "dhan":
        return parse_dhan(text)
    if fmt == "kite":
        return parse_kite(text)
    raise ValueError("unrecognised holdings export — expected a Dhan or Kite CSV")


def normalize_name(raw: str) -> str:
    """Company name reduced to its distinguishing tokens, for alias lookup.

    Only ever used to LOOK UP a confirmed alias, never to decide a match on its own:
    "THE GE SHPG.LTD" -> "GE SHPG" is nowhere near "Great Eastern Shipping Company",
    which is exactly why resolution needs the model plus a human confirmation.
    """
    cleaned = re.sub(r"[^A-Z0-9]+", " ", (raw or "").upper())
    tokens = [t for t in cleaned.split() if t and t not in _NOISE_TOKENS]
    return " ".join(tokens)


def resolve(snapshot: BrokerSnapshot, aliases: dict[str, str]) -> BrokerSnapshot:
    """Attach symbols using a confirmed alias table keyed by normalised name.

    Rows that do not resolve keep symbol=None and surface via `unresolved()`. No
    fuzzy fallback: a wrong symbol here becomes a wrong order.
    """
    lookup = {normalize_name(k): v for k, v in (aliases or {}).items()}
    out = []
    for h in snapshot.holdings:
        sym = h.symbol or lookup.get(normalize_name(h.raw_name))
        out.append(Holding(raw_name=h.raw_name, quantity=h.quantity,
                           avg_cost=h.avg_cost, ltp=h.ltp, symbol=sym))
    return BrokerSnapshot(broker=snapshot.broker, holdings=out,
                          file_as_of=snapshot.file_as_of,
                          account_fingerprint=snapshot.account_fingerprint,
                          content_hash=snapshot.content_hash)


def reconcile(target: dict[str, float], observed: dict[str, float],
              discretionary: dict[str, float] | None = None) -> dict:
    """Quantity-only comparison of the system's target book against the broker's.

    `discretionary` is a QUANTITY per symbol, not a symbol exclusion, so a name the
    user holds privately still reconciles correctly if the strategy later picks it:
    50 held privately + a target of 30 means buy 30, not "ignore this symbol".

    A symbol the broker holds that the system never targeted is `unclassified`, not
    a SELL -- it may be the user's own position, and must never be auto-queued for
    sale. Excess in a symbol the system DOES target is a genuine SELL.
    """
    disc = dict(discretionary or {})
    net: dict[str, float] = {}
    for sym, qty in (observed or {}).items():
        net[sym] = max(0.0, qty - disc.get(sym, 0.0))

    gaps, unclassified = [], []
    for sym, want in (target or {}).items():
        have = net.get(sym, 0.0)
        if want == have:
            continue
        gaps.append({"symbol": sym,
                     "action": "BUY" if want > have else "SELL",
                     "quantity": abs(want - have),
                     "target": want, "observed": have})
    for sym, qty in net.items():
        if sym not in (target or {}) and qty > 0:
            unclassified.append({"symbol": sym, "quantity": qty})

    gaps.sort(key=lambda g: -g["quantity"])
    unclassified.sort(key=lambda u: -u["quantity"])
    return {"gaps": gaps, "unclassified": unclassified,
            "in_sync": not gaps and not unclassified}
