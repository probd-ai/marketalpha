from datetime import date
import pandas as pd
from app.strategies.base import BacktestConfig
from app.strategies.signals import rs_line, trailing_slope_r2, rank_blend, roc


class RSMomentumStrategy:
    def select(
        self,
        close_panel: pd.DataFrame,
        nifty: pd.Series,
        members: set[str],
        as_of: date,
        cfg: BacktestConfig,
    ) -> dict[str, float]:
        cols = [s for s in close_panel.columns if s in members]
        hist = close_panel.loc[close_panel.index.date <= as_of, cols]

        slopes: dict[str, float] = {}
        r2s: dict[str, float] = {}
        for s in cols:
            series = hist[s].dropna()
            if len(series) < cfg.lookback + 1:
                continue
            rs = rs_line(series, nifty)
            slope, r2 = trailing_slope_r2(rs, as_of, cfg.lookback)
            if pd.notna(slope) and slope > 0:  # slope > 0 gate
                slopes[s] = slope
                r2s[s] = r2

        if not slopes:
            return {}
        scores = rank_blend(pd.Series(slopes), pd.Series(r2s))
        n_keep = max(1, round(cfg.nominal_size * cfg.top_pct))
        top = scores.sort_values(ascending=False).head(n_keep).index

        rocs: dict[str, float] = {s: roc(hist[s].dropna(), as_of, cfg.lookback) for s in top}
        roc_series = pd.Series(rocs).dropna().sort_values(ascending=False)
        selected = list(roc_series.head(cfg.top_n).index)
        if not selected:
            return {}
        w = 1.0 / len(selected)
        return {s: w for s in selected}
