"""Pre-trade checks (spec §7). Pure functions: each returns a Check with PASS / WARN / BLOCK and the evidence."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

import numpy as np
import pandas as pd

PASS, WARN, BLOCK = "PASS", "WARN", "BLOCK"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    items: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def last_weekday_before(today: date) -> date:
    return pd.Timestamp(np.busday_offset(today, -1, roll="backward")).date()


def freshness(signal_day: date, today: date, indicator_day: date | None) -> Check:
    expected = last_weekday_before(today)
    issues = []
    if signal_day < expected:
        issues.append(f"latest prices are for {signal_day}, expected {expected} (or a market holiday)")
    if indicator_day is not None and indicator_day < signal_day:
        issues.append(f"indicators end {indicator_day}, prices end {signal_day}")
    if not issues:
        return Check("Data freshness", PASS, f"prices and indicators current to {signal_day}")
    stale_days = int(np.busday_count(signal_day, expected))
    status = WARN if stale_days <= 1 and (indicator_day is None or indicator_day >= signal_day) else BLOCK
    return Check("Data freshness", status, "; ".join(issues) + (" — refresh data before trading" if status == BLOCK else
                                                               " — may be a market holiday"))


def coverage(sleeve: str, members: list[str], missing_price: list[str], missing_ind: list[str]) -> Check:
    miss = sorted(set(missing_price) | set(missing_ind))
    if not miss:
        return Check(f"Coverage · {sleeve}", PASS, f"all {len(members)} members priced and scored")
    share = len(miss) / max(len(members), 1)
    status = BLOCK if share > 0.05 else WARN
    return Check(f"Coverage · {sleeve}", status,
                 f"{len(miss)} of {len(members)} members lack a price or indicator on the signal day"
                 + (" — too many to trust the ranking" if status == BLOCK else " (halted or newly listed; excluded)"),
                 miss[:50])


def drift(mismatches: list[dict]) -> Check:
    if not mismatches:
        return Check("Drift", PASS, "replay reproduces every published week")
    return Check("Drift", BLOCK, "data changed under a published week — the replay no longer matches it; review and "
                                 "acknowledge before trading", mismatches[:50])


def caps(sleeve: str, weights: dict[str, float], cap: float, tol: float = 0.005) -> Check:
    over = {s: w for s, w in weights.items() if w > cap + tol}
    if not over:
        return Check(f"Cap · {sleeve}", PASS, f"every holding within {cap:.0%}")
    return Check(f"Cap · {sleeve}", BLOCK, f"{len(over)} holding(s) above {cap:.0%} after trading",
                 [{"symbol": s, "weight": w} for s, w in over.items()])


def concentration(sleeve: str, weights: dict[str, float], warn_at: float = 0.25) -> Check:
    big = {s: w for s, w in weights.items() if w > warn_at}
    if not big:
        top = max(weights.values(), default=0.0)
        return Check(f"Concentration · {sleeve}", PASS, f"largest holding {top:.1%} (no cap by design)")
    return Check(f"Concentration · {sleeve}", WARN, f"holding(s) above {warn_at:.0%} — winners run by design",
                 [{"symbol": s, "weight": w} for s, w in big.items()])


def cash(sleeve: str, cash_frac: float) -> Check:
    if cash_frac >= -0.005:
        return Check(f"Cash · {sleeve}", PASS, f"post-trade cash {cash_frac:.2%} of the sleeve")
    return Check(f"Cash · {sleeve}", BLOCK, f"post-trade cash {cash_frac:.2%} — orders exceed the money available")


def liquidity(lines: list[dict], adv: dict[str, float], limit: float = 0.05) -> Check:
    heavy = [{"symbol": l["symbol"], "value": l["value"], "adv20": adv.get(l["symbol"])}
             for l in lines if adv.get(l["symbol"]) and l["value"] > limit * adv[l["symbol"]]]
    missing = [l["symbol"] for l in lines if not adv.get(l["symbol"])]
    if heavy:
        return Check("Liquidity", WARN, f"{len(heavy)} order(s) above {limit:.0%} of 20-day average traded value — "
                                        f"split across sessions", heavy)
    if missing:
        return Check("Liquidity", WARN, "no traded-value history for some symbols", missing)
    return Check("Liquidity", PASS, f"every order below {limit:.0%} of its 20-day average traded value")


def instrument(sleeve: str, symbol: str, has_price: bool) -> Check:
    if has_price:
        return Check(f"Instrument · {sleeve}", PASS, f"{symbol} priced on the signal day")
    return Check(f"Instrument · {sleeve}", BLOCK, f"{symbol} has no price on the signal day")


def verdict(checks: list[Check]) -> str:
    s = {c.status for c in checks}
    return BLOCK if BLOCK in s else WARN if WARN in s else PASS
