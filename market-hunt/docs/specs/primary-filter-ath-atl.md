# Spec — Primary Filter (All-Time High / All-Time Low) + `none` momentum ranking

Status: implemented
Date: 2026-09-04

## Why

The simulator has exactly two selection stages: a **strategy filter** (indicator rules /
rich signal groups) and a **momentum ranking** that cuts the survivors to top-N. There is
no way to express "of the names my strategy likes, only consider the ones trading near
their all-time high" without writing a one-off script (`scripts/ath_filter_sim.py`).

This adds a third, explicitly-ordered stage — the **primary filter** — between the two,
and folds the `useAllEligible` checkbox into the momentum-ranking dropdown as `none`,
so the two controls stop contradicting each other.

## Stage order (the contract)

```
universe
  -> strategy filter        (rules / filter groups)
  -> PIT membership gate    (point_in_time_membership)
  -> per-stock Fib gate     (stock_gate, optional)
  -> PRIMARY FILTER         <-- NEW: ATH/ATL band, then optional top-50% rank cut
  -> book-level gates       (regime_gate / portfolio_gate — these empty the set)
  -> MOMENTUM RANKING       (top-N; `none` = no cut, hold everything above)
  -> rebalance / weighting
```

The primary filter is applied to the **whole qualified pool, current holdings included**.
A held name that falls out of the band is no longer in `qualified_stocks`, so it ranks
out of `select_top_stocks_by_momentum` and is sold at the next rebalance — identical in
mechanism to how the strategy filter and the per-stock Fib gate already evict names.
No separate carve-out, no path-dependent book.

## Parameters

| API field | Type | Default | Meaning |
|---|---|---|---|
| `primary_filter_take` | `"none" \| "top50" \| "all"` | `"none"` | **Master switch.** `none` skips the stage entirely. |
| `primary_filter_metric` | `"ath" \| "atl"` | `"ath"` | Which extreme the band is measured against. |
| `primary_filter_band` | float (percent) | `5.0` | Band width. `0` = must be exactly at the extreme. |

`take` is the master switch (not `metric`) so the control that turns the stage on is the
same control that says how much of the band survives — one dropdown, three states, no
redundant "None vs All" pair.

### Semantics

Let `ratio = close / extreme`, where `extreme` is the running extreme of CLOSE (see
"All-time window" below).

| metric | band keeps | `top50` ranks by | keeps |
|---|---|---|---|
| `ath` | `ratio >= 1 - band/100` | `ratio` **descending** (nearest the high first) | `ceil(n/2)` |
| `atl` | `ratio <= 1 + band/100` | `ratio` **ascending** (nearest the low first) | `ceil(n/2)` |

- `take="all"` → the band result, uncut.
- `take="top50"` → the band result, then the better half by proximity to the extreme.
- `ceil(n/2)`, not `floor`: a 1-name band must not select 0 names.
- Ties broken by symbol so a run is reproducible.
- A symbol with no price on the date, or no extreme yet, is **dropped** (it cannot be
  ranked honestly). This is a filter, not a scorer — silently scoring it 0 would let an
  unpriced name occupy a slot.

## All-time window (point-in-time correctness)

"All time" means **every bar the symbol has, from inception up to the current simulation
date — never a bar after it.**

The engine only loads prices for `[start_date, end_date]`, so a true ATH needs history
from before the window. Rather than pull ~2.5M docs (500 syms x 20y) over the wire, the
pre-window extreme is fetched as **one server-side `$group` per `prices_*` partition**:

```
{"$match": {"symbol": {"$in": syms}, "date": {"$lt": start_date}}}
{"$group": {"_id": "$symbol", "hi": {"$max": "$close_price"}, "lo": {"$min": "$close_price"}}}
```

That seed is then extended forward with a running max/min over the already-loaded
in-window closes (`build_extreme_series`). Result: exact all-time extremes at every
simulation date, at the cost of one cheap aggregation.

A symbol that IPO'd after `start_date` simply has no seed; its extremes start from its
first in-window bar. That is correct, not a gap.

## `momentum_ranking: "none"`

`none` means **no rank cut — hold every name that reaches this stage.** It is the
dropdown's default and replaces the `useAllEligible` checkbox.

- `momentum_ranking="none"` forces `max_holdings=None` (coerced, with a log line).
  The meaning is fully determined — "no ranking" cannot coexist with "keep the top N" —
  so coercion is correct here rather than a 400.
- `select_top_stocks_by_momentum` raises `ValueError` if it is ever handed
  `method="none"` together with a non-None `max_holdings`. Defence in depth: silently
  ranking by an all-zero score would produce an **alphabetical** top-N and look plausible.
- Legacy `useAllEligible=true` URLs still work: the run page maps them to `none`.

## Reporting

`summary.primary_filter` is `null` when the stage is off, else:

```json
{"metric": "ath", "band_percent": 5.0, "take": "top50",
 "avg_pool_before": 41.2, "avg_pool_after": 12.7, "days_emptied": 3}
```

`days_emptied` counts days the filter left the pool empty — the honest signal that a band
is too tight and the book is sitting in cash.

## Out of scope (v1)

- Wave engine: the primary filter is stateless-path only. A wave run ignores it.
- `multi-run` / `holdings-multi-run`: they keep their existing params; the filter defaults
  to off there. The single-run and detailed-view paths carry it.
- Extremes on high/low rather than close. Close-based matches `ath_filter_sim.py` and
  avoids intraday-spike ATHs that no strategy could have traded.
