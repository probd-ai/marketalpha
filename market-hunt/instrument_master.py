"""ISIN-keyed instrument resolution — pure logic, no I/O.

Why ISIN: a plan row is matched to a broker identifier so an order can be sent.
Matching on SYMBOL breaks the moment a company renames its ticker — the row
silently fails to resolve and drops out of the rebalance. ISIN survives renames,
so it is the primary key and symbol is the fallback.

⚠ ISINs are stable across renames but NOT across every corporate action
(amalgamation, some face-value changes). The master must be refreshed from the
same corporate-action feed that `market-refresh` uses; a stale ISIN resolves to
the wrong instrument, which is worse than not resolving at all.

Broker identifiers:
  dhan -> securityId   (numeric, from Dhan's scrip master)
  kite -> tradingsymbol (Kite orders by exchange tradingsymbol, not an id)

Both functions are pure so the resolution rules are testable without Mongo.
"""
from __future__ import annotations

BROKER_FIELD = {"dhan": "dhan_security_id", "kite": "kite_tradingsymbol"}

# When one identity appears in several series, EQ is the one we trade.
_SERIES_RANK = {"EQ": 0, "BE": 1, "BZ": 2, "SM": 3, "ST": 4, "": 5}


def _rank(doc: dict) -> int:
    return _SERIES_RANK.get((doc.get("series") or "").upper(), 9)


def build_index(docs: list[dict]) -> dict[str, dict]:
    """Two lookup tables from instrument docs, EQ preferred on collision."""
    by_isin: dict[str, dict] = {}
    by_symbol: dict[str, dict] = {}
    for d in docs:
        isin = (d.get("isin") or "").strip().upper()
        sym = (d.get("symbol") or "").strip().upper()
        if isin and (isin not in by_isin or _rank(d) < _rank(by_isin[isin])):
            by_isin[isin] = d
        if sym and (sym not in by_symbol or _rank(d) < _rank(by_symbol[sym])):
            by_symbol[sym] = d
    return {"by_isin": by_isin, "by_symbol": by_symbol}


def resolve(rows: list[dict], index: dict[str, dict],
            broker: str) -> tuple[dict[str, str], list[str]]:
    """Map each row's SYMBOL to that broker's identifier, resolving via ISIN first.

    Returns `{symbol: identifier}` — keyed by symbol so the execution batch can
    keep looking rows up by `row["symbol"]` — plus one reason string per row that
    could not be resolved. An unresolved row is REFUSED upstream, never guessed.
    """
    field = BROKER_FIELD.get(broker)
    if not field:
        return {}, [f"unknown broker '{broker}'"]

    out: dict[str, str] = {}
    unresolved: list[str] = []
    for row in rows:
        sym = (row.get("symbol") or "").strip()
        isin = (row.get("isin") or "").strip().upper()
        doc = index["by_isin"].get(isin) if isin else None
        via = "isin"
        if doc is None:
            doc = index["by_symbol"].get(sym.upper())
            via = "symbol"
        if doc is None:
            unresolved.append(
                f"{sym}: not in the instrument master (isin={isin or 'none'})")
            continue
        ident = doc.get(field)
        if not ident:
            unresolved.append(
                f"{sym}: instrument found via {via} but it has no {broker} identifier")
            continue
        out[sym] = str(ident)
    return out, unresolved
