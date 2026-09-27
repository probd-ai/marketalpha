"""Band-ratio analytics for an index over time.

A distribution says how many names sit in each score band. A *ratio* of two bands says
how the mass is tilted between them, which reads the same whether the index has 50
members or 750 — that is what makes it comparable across indices and across time.

Everything here is pure so it can be tested without a database. The router supplies the
per-date band counts (produced by one Mongo $group, not a Python loop) and the membership
intervals; this module decides the bands, the epochs and the arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

# The five fixed score bands. Half-open [lo, hi) except the last, which is closed, so a
# score of exactly 100 lands in 80-100 rather than falling out of the histogram.
BANDS: tuple[tuple[int, int], ...] = ((0, 20), (20, 40), (40, 60), (60, 80), (80, 100))

# The four ratios the page shows: three adjacent-band tilts walking down the scale, plus
# one wide strength-vs-weakness ratio.
RATIO_PAIRS: tuple[tuple[str, str], ...] = (
    ("80-100", "60-80"),
    ("60-80", "40-60"),
    ("40-60", "20-40"),
    ("80-100", "20-40"),
)

VALID_METRICS = ("truevx_score", "mean_short", "mean_mid", "mean_long")

LOOKBACKS: dict[str, int | None] = {
    "1Y": 365, "2Y": 2 * 365, "3Y": 3 * 365, "5Y": 5 * 365, "10Y": 10 * 365, "MAX": None,
}


def band_label(lo: int, hi: int) -> str:
    return f"{lo}-{hi}"


def band_labels() -> list[str]:
    return [band_label(lo, hi) for lo, hi in BANDS]


def ratio_key(num: str, den: str) -> str:
    return f"{num}/{den}"


def ratio_keys() -> list[str]:
    return [ratio_key(n, d) for n, d in RATIO_PAIRS]


def band_switch(field: str) -> dict:
    """A Mongo $switch that maps a metric value to its band label.

    Bucketing server-side is the whole speed story: Mongo returns one row per
    (date, band) instead of one document per (symbol, date).
    """
    branches = [{"case": {"$lt": [field, hi]}, "then": band_label(lo, hi)}
                for lo, hi in BANDS[:-1]]
    return {"$switch": {"branches": branches, "default": band_label(*BANDS[-1])}}


def compute_ratios(counts: dict[str, int],
                   pairs: tuple[tuple[str, str], ...] = RATIO_PAIRS) -> dict[str, float | None]:
    """Numerator count / denominator count per pair.

    A zero denominator yields None, never 0 and never infinity: "no names in the
    denominator band" is missing information, and charting it as zero would draw a
    confident line through a hole in the data.
    """
    out: dict[str, float | None] = {}
    for num, den in pairs:
        d = counts.get(den, 0)
        n = counts.get(num, 0)
        out[ratio_key(num, den)] = round(n / d, 4) if d else None
    return out


def resolve_window(lookback: str | None,
                   start_date: str | None,
                   end_date: str | None,
                   today: date) -> tuple[date, date]:
    """Explicit dates win; otherwise walk `lookback` back from today."""
    end = datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else today
    if start_date:
        return datetime.strptime(start_date, "%Y-%m-%d").date(), end
    days = LOOKBACKS.get((lookback or "5Y").upper(), LOOKBACKS["5Y"])
    if days is None:
        return date(2000, 1, 1), end
    return end - timedelta(days=days), end


@dataclass(frozen=True)
class Epoch:
    start: date
    end: date          # exclusive
    members: tuple[str, ...]


def membership_epochs(intervals, start: date, end: date, members_as_of) -> list[Epoch]:
    """Split [start, end] into spans over which the member set does not change.

    Membership is piecewise-constant between reconstitution events, so a five-year
    window is ~20 epochs. One indexed query per epoch is point-in-time correct AND
    fast — far cheaper than filtering per symbol per date in Python.
    """
    edges = {start, end}
    for iv in intervals:
        for e in (iv.effective_from, iv.effective_to):
            if e is not None and start < e < end:
                edges.add(e)
    ordered = sorted(edges)
    epochs: list[Epoch] = []
    for lo, hi in zip(ordered, ordered[1:]):
        members = tuple(sorted(members_as_of(intervals, lo)))
        if members:
            epochs.append(Epoch(lo, hi, members))
    return epochs


def assemble(per_date_counts: dict[str, dict[str, int]],
             prices: dict[str, dict] | None = None) -> list[dict]:
    """Turn {date: {band: count}} into the row list the page charts."""
    rows = []
    for ds in sorted(per_date_counts):
        counts = {lbl: int(per_date_counts[ds].get(lbl, 0)) for lbl in band_labels()}
        row = {"date": ds,
               "total": sum(counts.values()),
               "counts": counts,
               "ratios": compute_ratios(counts)}
        if prices and ds in prices:
            row["price"] = prices[ds]
        rows.append(row)
    return rows
