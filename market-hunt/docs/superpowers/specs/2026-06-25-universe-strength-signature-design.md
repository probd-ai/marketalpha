# Universe Strength Signature (RS distribution heatmap) — Design

**Date:** 2026-06-25 · **Repo:** market-hunt

## Goal

Visualize how a universe's relative-strength distribution evolves over time — its "signature".
For a chosen universe + metric, show a **time × value heatmap**: X = month, Y = 0–100 bins,
color = % of the universe's stocks whose metric sits in that bin that month.

## Decisions (brainstorm)

- **Time view:** time-evolving **heatmap** (monthly), with a date slider → that month's histogram.
- **Metric:** `mean_long` default; selector also offers `mean_mid`, `mean_short`, `truevx_score`.
- **Universes:** NIFTY50 / NIFTY100 / NIFTY500, **current members** (from `index_meta`).

## §1 Backend — `GET /api/analytics/strength-distribution`

Params: `universe` (NIFTY50/100/500), `metric` (default mean_long), `start`, `end`, `bins` (default 20).

Logic:
1. Members = `[d["Symbol"] for d in index_meta.find({"index_name": <normalized universe>}, {"Symbol":1})]`.
   Normalize `"NIFTY 500" → "NIFTY500"`. 404 if none.
2. Pull `indicators.find({indicator_type:"truevx", symbol:{$in:members}, date:{$gte:start,$lte:end}},
   {symbol,date,"data.<metric>"})`.
3. **Resample monthly:** keep each symbol's LAST value per `YYYY-MM`.
4. Per month, bin members' values into `bins` equal buckets over [0,100] → counts + % of that month's
   reporting members. Also month mean & median.

Pure, unit-tested helper: `bin_strength_values(values, bins) -> (counts, pct)` (value 100 → last bin).

Response:
```
{ universe, metric, n_members, bins,
  bin_edges: [0, 5, …, 100],
  months: [ {date:"YYYY-MM", n, counts:[…], pct:[…], mean, median}, … ] }
```
Perf: N50 fast; N500 pulls more docs (acceptable for an analytics page; caching is a later optimization).

## §2 Frontend — `analytics/distribution` page (linked from Analytics hub)

- Controls: universe · metric · date range · bin count.
- **Heatmap** (custom lightweight SVG/div grid — no new dependency): columns = months, rows = bins
  (0 at bottom → 100 at top), cell color = sequential indigo ramp by % (relative to the column/global max).
  Sparse month labels on X, bin labels on Y.
- **Click a column (or use a slider)** → render that month's **histogram** (horizontal bars) + KPI line
  (members reporting, mean, median).
- Empty/loading/error states.

## §3 Testing (live API + lint)

- API: each month's `pct` sums ≈ 100 (±rounding); `len(counts)==bins`; N50 `n_members`≈50; `bin_edges` correct.
  Spot-check a month's mean is in [0,100].
- Unit: `bin_strength_values` — boundary (0 → bin 0, 100 → last bin), pct sums to 100.
- Frontend: tsc/eslint clean on the new page; route 200; heatmap renders.

## Non-goals

- PIT membership (current snapshot only, per decision).
- Cross-universe overlay (one universe at a time; switch via selector).
- Caching/precompute (later if N500 latency bites).
