# Spec — Tier-bucketed capital allocation

Status: **REJECTED, not built** · 2026-08-17

> Rejected the same day it was drafted, deliberately — keep this document as the record
> of why, so the idea is not re-proposed from scratch.
>
> **Why rejected.** Capacity control — the only argument that justified the complexity —
> is already handled at the *account* level by a separate large-cap portfolio with its
> own capital base. Enforcing it again inside this portfolio solves the same problem
> twice. Equal weight across the full selection lets the tier mix float to ~67/33
> micro-small vs mid-large, which is sufficient mid/large exposure, arrived at by the
> ranking rather than imposed, and it self-normalises single-name concentration with no
> cap and no cash drag.
>
> **The variant worth remembering** if this is ever revisited: fixed *slots* per bucket
> (top K_A micro/small + top K_B mid/large from one full-universe ranking) is the only
> formulation that pins tier exposure without either concentration or cash drag. A
> per-name cap inside a bucket does not work — it converts concentration into idle cash.
>
> **Caveat on the numbers that motivated this.** The tier attribution that made mid/large
> look more productive per rupee is computed with *current* tier labels applied to the
> full history (`_symbol_meta_for`, `app/routers/portfolio.py:190`). Momentum winners are
> promoted upward through cap tiers, so their gains are credited to the tier they ended
> in. That bias inflates mid/large productivity. The PIT tier resolver described below is
> still worth building **for attribution alone**, independent of this feature.

## Problem

Capital is allocated **per sleeve**, never per cap tier. A sleeve ranks its universe,
takes the top N, and equal-weights them — so the tier mix of the book is an *outcome*
of the ranking, not a decision. On `Alpha-Market-probdx0.1` (universe
`NIFTY500MICRO750`) that lands ~67% of capital in Microcap+Smallcap and ~33% in
Midcap+Large, drifting run to run.

Two things follow:

- **Capacity.** The microcap share is the part that cannot absorb size. It is also the
  part carrying the most capital, so the book's ceiling is set by its least liquid leg.
- **Control.** The operator cannot say "₹11L rides micro/small, ₹8L rides mid/large"
  without changing the universe — and changing the universe changes the *picks*, which
  defeats the purpose.

## What this is NOT

Running the strategy separately on `NIFTYMIDCAP150` / `NIFTYSMALLCAP250` is a different
strategy. Ranking 20 slots out of 150 names selects differently from ranking 20 out of
744 — the observed tier performance would not carry over. **The picks must not change.**

## Semantics

One ranking, many books:

1. Rank the **full universe** exactly as today. Select the top `max_holdings`.
2. **Partition** that selection by the cap tier of each name *as of that rebalance date*.
3. Each bucket funds only its slice, from its own capital, and **compounds
   independently** — i.e. a bucket is its own book, not a fixed weight inside one book.

A bucket holding 7 of the top 20 equal-weights those 7 across its own capital. Position
sizing is therefore tier-dependent by construction; that is the intent, and §Risks
covers the sharp edge it creates.

### Why this needs no new allocation engine

A portfolio is already *N capital-weighted sleeves, each an independent book*. A
tier bucket is a sleeve with one extra constraint. The feature is therefore a single
new sleeve field plus a point-in-time tier resolver:

```
Sleeve.tiers: list[str] = []      # [] == all tiers (today's behaviour, unchanged)
```

Two sleeves, same `strategy_id` + same `universe`, disjoint `tiers`, weights 57.89 /
42.11 reproduce the ₹11L / ₹8L split on a ₹19L portfolio. Composition, attribution,
rebalance history, preview and the curve all keep working untouched.

**The filter applies AFTER selection, never before.** Filtering the universe before
ranking would change the picks — see §What this is NOT.

## Point-in-time tier resolution (correctness-critical)

`_symbol_meta_for()` (`app/routers/portfolio.py:190`) resolves tier from the **current**
`index_meta` snapshot. That is fine for labelling today's book, but routing *historical*
capital by it is look-ahead: a name that is Midcap 150 today may have been a microcap in
2022, and funding it from the "large" bucket back then is a decision no operator could
have made.

Tier must therefore be resolved **as of the rebalance date**, from the membership
intervals `membership.load_membership(db, index_name, anchor)` already builds off
`index_reconstitution`:

- Tier precedence (most specific wins, as today): Nifty 50 ⊂ Nifty 100 · Midcap 150 ·
  Smallcap 250 · Microcap 250 · Nifty 500 (other) · Other.
- A name in no tracked index on that date is `Other`.
- Indexes lacking reconstitution data cannot be resolved point-in-time. With PIT on this
  **fails loud** (consistent with the existing universe behaviour), never silently falls
  back to the current snapshot.

## Configuration

Per sleeve:

| Field | Default | Meaning |
|---|---|---|
| `tiers` | `[]` | Tier labels this sleeve may hold. `[]` = unrestricted (back-compatible). |
| `max_position_percent` | `null` | Optional cap on any single name as % of that sleeve's capital. |
| `idle_capital` | `"cash"` | `cash` \| `spill` — what happens when the selection contains no name in this sleeve's tiers. |

`idle_capital: "cash"` holds the bucket flat for that period. `spill` releases the
bucket's capital to the other sleeves pro-rata for that period — which quietly re-floats
the tier mix toward today's behaviour, so `cash` is the default and `spill` must be
chosen deliberately.

## Validation

- Tier labels must come from the known set; unknown labels are rejected at create/update.
- Overlapping `tiers` across sleeves of the same strategy+universe is **allowed but
  warned** — the same pick would be funded twice.
- `tiers` non-empty on a universe with no PIT membership + `point_in_time_membership:true`
  → reject with the existing fail-loud message.

## Risks this introduces

1. **Concentration.** A bucket that receives 3 of 20 picks puts a third of its capital in
   each. `max_position_percent` is the mitigation; unset, the book can get very
   concentrated in thin periods.
2. **Cash drag.** A bucket with no eligible picks earns nothing that period. Visible, by
   design — `spill` is the escape hatch.
3. **Attribution reads differently.** Sleeve attribution becomes tier attribution; the
   existing `allocation.index_tier` breakdown will largely duplicate it.

## Out of scope (this pass)

Live order placement. The UI surfaces the per-bucket trade list; sending it to a broker
is a separate deliverable.
