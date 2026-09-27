"""Fund layer: combine sleeve equity curves at target weights with a monthly reset (spec §4.3).

Each sleeve is replayed on its own (fund.engine.replay). Every sleeve rule sizes in proportion to its equity, so
resizing a sleeve at the monthly reset is the same as multiplying its later curve by a constant. The fund therefore
tracks, per sleeve, a SCALE factor: rupees the fund holds in the sleeve / the sleeve's stand-alone value. Model
orders times the scale give the fund's real order sizes; a change in scale on a reset day is the reset trade.

`blend` reproduces fund_report_data.blend exactly (the report's whole-fund curve).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

MODEL_START_FLOOR = date(2020, 10, 1)       # earliest date with validated PIT membership for all stock universes


def validate_model_start(model_start: date, today: date) -> None:
    if not (MODEL_START_FLOOR <= model_start <= today):
        raise ValueError(f"model_start must be between {MODEL_START_FLOOR.isoformat()} and {today.isoformat()} "
                         f"(got {model_start.isoformat()})")


@dataclass
class FundResult:
    equity: pd.Series            # fund value, normalised to 1 at the start (multiply by capital for rupees)
    sleeve_values: pd.DataFrame  # value per sleeve, same normalisation
    scale: pd.DataFrame          # sleeve_value / sleeve stand-alone curve (normalised to 1 at start)
    reset_cost: float            # total reset cost, same normalisation
    reset_days: list


def blend(curves: pd.DataFrame, weights: dict[str, float], reset_cost: float = 0.0015,
          reset_days: set | None = None, band_days: set | None = None, band: float | None = None) -> FundResult:
    """curves: one column per sleeve (any scale), aligned on the fund calendar and forward-filled.
    reset_days: sessions on which weights are always restored; default = first session of each month (the report).
    band_days / band: on these sessions weights are also restored when any sleeve is more than `band` (weight points,
    e.g. 0.05) away from its target — the safety net between scheduled rebalances."""
    cols = list(weights)
    px = curves[cols]
    w = [weights[c] for c in cols]
    r = px.pct_change().fillna(0).to_numpy()
    month = px.index.to_period("M")
    v = list(w)
    out, vals, cost, resets = [1.0], [list(v)], 0.0, []
    for i in range(1, len(px)):
        v = [v[j] * (1 + r[i, j]) for j in range(len(v))]
        tot = sum(v)
        d = px.index[i]
        scheduled = (d in reset_days) if reset_days is not None else (month[i] != month[i - 1])
        drifted = (band is not None and band_days is not None and d in band_days
                   and max(abs(v[j] / tot - w[j]) for j in range(len(v))) > band)
        if scheduled or drifted:
            moved = sum(abs(v[j] - w[j] * tot) for j in range(len(v))) / 2
            c = moved * reset_cost
            cost += c
            tot -= c
            v = [w[j] * tot for j in range(len(v))]
            resets.append(px.index[i])
        out.append(tot)
        vals.append(list(v))
    sv = pd.DataFrame(vals, index=px.index, columns=cols)
    norm = px / px.iloc[0]
    return FundResult(pd.Series(out, index=px.index), sv, sv / norm, cost, resets)
