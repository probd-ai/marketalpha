"""RSZ — Relative Strength Z: a continuous, cross-sectionally ranked strength score.

Why this exists (what it fixes in `truevx`)
-------------------------------------------
`truevx` answers three near-binary questions ("is the relative line above the 23%
fib at 22/66/222 days?") through a tanh whose deadband is 2% of the trailing range.
tanh saturates at ~6% of range, so every strongly-trending stock scores identically:
7.3% of all readings are EXACTLY 100.0 and the top decile is a mass tie. A tie
cannot be ranked, so `truevx_score` is a filter masquerading as a score.

RSZ keeps the good idea (relative strength across three horizons) and removes the
three things that broke it:

  1. NO SATURATION. The per-horizon statistic is an information ratio

         z_n = ln(rel_t / rel_{t-n}) / (sigma_n * sqrt(n))

     i.e. "how many standard deviations of drift has this stock put on its
     benchmark over n sessions". Continuous, dimensionless, magnitude-preserving,
     and comparable across stocks and volatility regimes.

  2. NO TREND-COLOUR TERM. Half of truevx's dynamic range came from the slope of a
     step-function threshold (+1/-1/0 for "fib rising 2 bars"). Prior research
     already refuted the velocity axis; only strength paid. It is gone.

  3. A SORTABLE CROSS-SECTION. The score is a percentile of a continuous statistic
     across the whole scored universe, recomputed every session, so the top of the
     book can actually be ordered. Benchmark stays Nifty 50 for every stock, exactly
     like truevx, so the two can be read side by side.

Per-segment benchmarking (scoring a midcap against Midcap 150) was built and tested.
It did not change outcomes and it made RSZ incomparable to truevx, so it was removed.

WHAT RSZ IS AND IS NOT FOR
--------------------------
Tested as a stock RANKER across 45 backtests: it wins no universe consistently and
does not beat a plain price return. Do not use it to sort a book — that is what
`momentum_ranking` price methods are for. Its useful output is the VOLATILITY it
measures on the way: among names that are already strong, it separates the smooth
compounders from the jumpy ones. That split is what the analytics page serves.

Reproducibility: every statistic is a plain rolling window over exactly the last
n+1 closes. There is no EMA seeded at data[0], so a value never changes because the
series was loaded from a different start date. Emit nothing until the window is full.

All functions here are pure. Storage/orchestration lives in `rsz_compute.py`.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

# One benchmark for every stock — same frame of reference as truevx.
BENCHMARK = "Nifty 50"

# Segment is retained as a LABEL only (which cap bucket a name sits in), never as a
# benchmark or a ranking pool.
SEGMENT_BENCHMARK: dict[str, str] = {
    "NIFTY50":          "Nifty 50",
    "NIFTY100":         "Nifty 100",
    "NIFTYMIDCAP150":   "Nifty Midcap 150",
    "NIFTYSMALLCAP250": "Nifty Smallcap 250",
    "NIFTYMICROCAP250": "Nifty Microcap 250",
    "NIFTY500":         "Nifty 500",
}
# Checked in this order; first hit wins, so a Nifty 50 name is never scored as a
# midcap just because it also sits in a broader index.
SEGMENT_PRIORITY: tuple[str, ...] = (
    "NIFTY50", "NIFTY100", "NIFTYMIDCAP150",
    "NIFTYSMALLCAP250", "NIFTYMICROCAP250", "NIFTY500",
)
FALLBACK_SEGMENT = "NIFTY500"


@dataclass(frozen=True)
class RszParams:
    """Frozen parameter set. Its hash keys stored rows, so a future re-parameterization
    can coexist with today's rows instead of overwriting them.

    Benchmark is Nifty 50 for every stock and the cross-section is the whole scored
    universe — deliberately the same frame of reference as `truevx`, so RSZ and the
    TrueVX metrics can be read side by side without mentally re-basing either one.
    Per-segment benchmarking was tested and dropped: it changed nothing that mattered
    and made the two indicators incomparable.
    """
    horizons: tuple[int, int, int] = (22, 66, 222)
    weights: tuple[float, float, float] = (0.5, 1.0, 1.5)   # short, mid, long
    aligned_rank_floor: float = 70.0   # a horizon "agrees" at/above this percentile
    min_universe_size: int = 20        # below this a cross-section can't be ranked
    vol_window: int = 66               # sessions used for the smooth/volatile split

    @property
    def hash(self) -> str:
        payload = json.dumps({
            "horizons": list(self.horizons),
            "weights": list(self.weights),
            "aligned_rank_floor": self.aligned_rank_floor,
            "min_universe_size": self.min_universe_size,
            "vol_window": self.vol_window,
            "benchmark": BENCHMARK,
            "version": 2,
        }, sort_keys=True)
        return hashlib.sha1(payload.encode()).hexdigest()[:12]


def segment_for(index_names: set[str]) -> str:
    """The most specific segment a symbol belongs to."""
    for seg in SEGMENT_PRIORITY:
        if seg in index_names:
            return seg
    return FALLBACK_SEGMENT


def rel_series(stock_closes: list[float], bench_closes: list[float]) -> list[float]:
    """Relative price line. Both inputs must already be aligned on the same dates."""
    if len(stock_closes) != len(bench_closes):
        raise ValueError("stock and benchmark series must be the same length")
    out = []
    for s, b in zip(stock_closes, bench_closes):
        out.append(s / b if (b and b > 0 and s and s > 0) else math.nan)
    return out


def info_ratio(rel: list[float], n: int) -> list[float]:
    """z_n[t] = ln(rel_t / rel_{t-n}) / (sigma_n * sqrt(n)), NaN until the window fills.

    sigma_n is the sample stdev of the last n daily log changes of `rel`. Dividing
    by sigma*sqrt(n) turns a raw excess return into a t-like statistic, which is
    what makes two stocks with different volatilities comparable.
    """
    T = len(rel)
    out = [math.nan] * T
    if n < 2 or T <= n:
        return out
    # daily log changes of the relative line; d[t] pairs with rel[t]
    d = [math.nan] * T
    for t in range(1, T):
        a, b = rel[t - 1], rel[t]
        if a and b and a > 0 and b > 0 and not (math.isnan(a) or math.isnan(b)):
            d[t] = math.log(b / a)
    for t in range(n, T):
        win = d[t - n + 1: t + 1]
        if any(math.isnan(x) for x in win):
            continue
        r0, r1 = rel[t - n], rel[t]
        if not r0 or not r1 or r0 <= 0 or r1 <= 0 or math.isnan(r0) or math.isnan(r1):
            continue
        mean = sum(win) / n
        var = sum((x - mean) ** 2 for x in win) / (n - 1)
        sigma = math.sqrt(var)
        if sigma <= 0:
            continue
        out[t] = math.log(r1 / r0) / (sigma * math.sqrt(n))
    return out


def composite_z(zs: float, zm: float, zl: float, weights: tuple[float, float, float]) -> float:
    """Weight-blended z. Long-weighted by default: prior work found the 222d average
    carried the highest information coefficient of the three horizons."""
    if any(math.isnan(v) for v in (zs, zm, zl)):
        return math.nan
    ws, wm, wl = weights
    tot = ws + wm + wl
    if tot == 0:
        return math.nan
    return (ws * zs + wm * zm + wl * zl) / tot


def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    """Cross-sectional percentile in [0,100] over one date's segment.

    Average-rank tie handling, so genuinely equal values share a rank instead of
    being ordered arbitrarily. With a continuous statistic real ties are ~absent —
    which is the whole point versus truevx's mass tie at 100.
    """
    clean = {k: v for k, v in values.items() if v is not None and not math.isnan(v)}
    n = len(clean)
    if n == 0:
        return {}
    if n == 1:
        return {next(iter(clean)): 50.0}
    ordered = sorted(clean.items(), key=lambda kv: kv[1])
    ranks: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        avg_idx = (i + j) / 2.0            # 0-based average position of the tie block
        pct = avg_idx / (n - 1) * 100.0
        for k in range(i, j + 1):
            ranks[ordered[k][0]] = pct
        i = j + 1
    return ranks


def aligned_count(r_short: float, r_mid: float, r_long: float, floor: float) -> int:
    """How many of the three horizons agree that this name is strong.

    Alignment is a first-class output because a single blended number cannot express
    it: a mid composite can mean 'long+mid strong, short weak' or the reverse, and
    those are different trades.
    """
    return sum(1 for r in (r_short, r_mid, r_long)
               if r is not None and not math.isnan(r) and r >= floor)
