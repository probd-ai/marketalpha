# RS / TrueVX Strategy Research Log

> A precise, human-readable record of what we tested on the TrueVX relative-strength
> indicator, what each experiment found, and the principles that survived. Every result
> below is **in-sample 2019-01 → 2026-06** unless marked otherwise. Numbers are **net of
> Indian statutory delivery cost** (~22 bps round-trip) and **point-in-time correct**
> (survivorship-bias-free universe reconstruction).

---

## TL;DR (the verdict)

Durable, multi-timeframe relative strength is a strong **eligibility gate** — not a ranker.
Used to *define who is tradeable* (and then held broadly, equal-weight), it beats the honest
benchmark on return, Sharpe, **and** drawdown. Used to *rank and concentrate* (pick the
top-K of the qualifying set), it loses — monotonically. The single best variant narrows the
~500-name universe to an average **79 names (~16%)**, but that fraction is an **output that
floats with the regime (27–172)**, not a quota to impose. The size of the qualifying set is
itself a market-timing signal — and that, more than stock selection, is where the
risk-adjusted edge comes from.

**Champion:** multi-timeframe aligned-stack gate, hold-all eligible, equal-weight, monthly →
**23.8% CAGR / Sharpe 1.14 / −26% maxDD** vs **17.3% / 0.85 / −36%** for the equal-weight
universe.

> ⚠ **Read with the regime nuance — exp #16–17.** The edge is real out-of-sample but **lumpy and
> regime-contingent.** Walk-forward (#17) OOS 2021–26: fixed champion **24.6% / Sharpe 1.28 beats
> control 19.2% / 1.10** (+5.4pts) — and the **thresholds are interchangeable, not overfit**
> (tuning them adds nothing; structure carries it). BUT the edge is **carried by 2021 & 2024 and
> *lost* in 2023 & 2025** (a narrow-momentum bet: wins narrow-leadership/trending regimes, lags
> broad rallies). And the **drawdown advantage does NOT persist** — OOS maxDD ties the control
> (−24 vs −24); the earlier −26% edge was a 2020-crash artifact. **Honest forward expectation:
> ~+5pts in trending regimes, ~parity-or-worse in broad ones — not a steady 23.8%, and no
> reliable drawdown protection outside crashes.**

---

## 1 · Setup

| | |
|---|---|
| **Indicator** | TrueVX: `truevx_score` (0–100 RS), `mean_short` (22d SMA of score), `mean_mid` (66d), `mean_long` (222d) |
| **Universe** | NIFTY 500, reconstructed point-in-time (`membership.py` → `members_as_of`) — no survivorship bias |
| **Data** | MongoDB `market_hunt`; daily closes in `prices_YYYY_YYYY`; indicators in `indicators` |
| **Rebalance** | Monthly, first trading day; signals on prior data only (no look-ahead) |
| **Weighting** | Equal-weight all eligible names (no top-K unless a test explicitly concentrates) |
| **Cost** | STT + NSE txn + SEBI + stamp + GST, zero brokerage → BUY ≈ 11.9 bps, SELL ≈ 10.4 bps, applied to turnover |
| **Metrics** | CAGR (months/12 annualization), Sharpe (×√12), max drawdown |

### The benchmark correction (read this first)

The honest benchmark is **EW-all-N500** — the *same universe, equal-weighted, no filter*
(**17.3% CAGR**) — **not** the cap-weighted Nifty 50 (**10.8%**). ~6 points of any apparent
"edge" vs Nifty is just equal-weight / size beta, not skill. **Every strategy below is judged
against EW-all-N500.** Beating Nifty proves nothing; beating the equal-weight universe is the
real bar.

---

## 2 · Experiments (chronological)

Each row: the thought that motivated it → what we did → what we found.

| # | Hypothesis / thought | Method | Verdict |
|---|---|---|---|
| 1 | "Divide 0–100 score into 5 zones, take top-2 each → 10-stock portfolio; there must be clusters." | PCA + clustering on the indicator fields | **Reframed.** Indicator is **2 orthogonal axes — strength × velocity (4 regimes)**, not 5 score-zones. Score piles at **80–100**, not at 50. |
| 2 | "Does the discovered structure predict forward returns?" | Forward-return test per regime | **Velocity refuted.** Only the strength axis shows forward-return separation; velocity does not. |
| 3 | "Run a proper factor test, net of costs." | Fama-MacBeth cross-sectional regression | **Velocity dead: t ≈ −0.91** (no edge, faint reversal). Strength/RS carries the signal. "Don't trade a t = −0.91." |
| 4 | "Is the RS-momentum edge robust out-of-sample?" | Built a full **walk-forward validator** (rolling folds, Sharpe-selected size+window vs fixed-N baseline) | **ROBUST_FIXED.** Edge is real OOS on *return* (fixed N≈20 beats Nifty, 16% vs 10.2% CAGR), but **optimizing N overfits**; risk-adjusted edge is thin. (Found a sim `sharpe=inf` bug — logged.) |
| 5 | "Buy when `mean_short` crosses up through a zone, sell on cross down; hold all eligible." | Zone-cross state machine on `mean_short` {20/40/60/80/90} | **Wash.** 16.3% ≈ control 17.3% (worse after turnover). Fast signal doesn't pay — consistent with #3. |
| 6 | "Same, but on the slow `mean_long`." | Zone-cross state machine on `mean_long` | **First real edge.** **20.8% / 1.01 / −34%** — beats control by +3.5 pts, barely turns over. Durable strength pays. |
| 7 | "Buy weakness turning up: cross-up-10, sell at 90 (TG) or back below 10 (SL)." | `mean_short` 10/90 rule | **Worst.** 11.9% / 0.62 / −42% — loses to control by 5.4 pts. Buying weakness / holding mediocrity is value-destructive. |
| 8 | "Multi-peeling: re-apply the filter to the survivors — strongest-of-strong should do better." | Split `mean_long` eligibles into terciles by level | **Refuted.** Top tercile 20.1% **underperforms** full eligible 21.1%; cutting breadth 210→70 added no Sharpe. Peeling again hurts (no added IC → no IR gain). |
| 9 | "Stack all timeframes bullish: long>50 & mid>60 & short>80 & score>90." | Static 4-way AND gate, hold-all | **WINNER. 23.8% / 1.14 / −26%** — best on all three axes. (I predicted it would *lag* from over-extension. I was wrong.) |
| 10 | "Concentrate the winner: take top-N of the stack by price ROC." | Rank aligned-stack eligibles by 66d ROC, keep top-N | **Monotonic degradation.** top10 12.7% < top20 18.4% < top30 21.2% < hold-all(79) 23.8%. ROC picks the most over-extended → they reverse. |
| 11 | "Tightest gate: `mean_short` crosses up through `mean_mid`, both >90; exit when short <90." | Crossover state machine, hold-all in-position | **Good gate, over-narrowed.** 19.7% / 0.93 / −29% (avg 25 names). Lands at the same ~0.93 Sharpe as any 25–30 name book — breadth drag. |
| 12 | "Pick the 5–6yr multibaggers, read off the RS *trajectory* (signature) they traced — then mimic it." | Winner autopsy: top-12 by 2019→2025 price multiple, extract RS path + descriptive stats (survivorship *by design*, hypothesis-gen only) | **Signature found.** Winners lived at `mean_long`≈67 (mid-band, **not** pinned at 90–100), climbed staircase-style (rising ~half the months), and **"breathed" ~12×** — fast-RS dipped ~14 pts below trend and recovered, trend intact. Suggested a "buy the inhale" thesis → tested in #13. |
| 13 | "Do the RS *waves* predict price? Build the model." | Decompose RS into trend (`mean_long` level+slope) + oscillator (`breath`=`mean_short`−`mean_long`); forward-return surface + Fama-MacBeth over full PIT N500 (no survivorship: all names/months are rows, fwd-return is the label) | **Mean-reversion REFUTED; "buy the inhale" is wrong.** Deep inhale = *lowest* fwd 3m return in every trend band; extended/exhale = *highest* (momentum continuation). Phase timing adds ~0 (6.5% vs 6.1%). FM: only firm predictor is **trend slope** `ml_slope` (t=2.31), level marginal (t=1.94), **breath ≈ 0** (t=0.54). The breath is a *symptom* of a durable trend, not a timing edge. |
| 14 | "Is `ml_slope` persistent — does an accelerating trend keep accelerating? (make-or-break for a trend-dynamics model)" | Rank-IC of slope vs its own future + quintile transition + run-length/hazard + forward-return decay, full PIT N500 | **PERSISTENT & tradeable — first coherent signal.** Slope rank-IC: 0.50 at 3m (clean, non-overlapping), 0.18 at 6m, **−0.41 at 12m** (matures & reverses). Quintile transition monotone & preserved (Q1 −6.3 → Q5 +7.2 at +3m). Rising-trend run: median **5mo**, hazard .85–.92 *rising* mid-life, rolls over ~10–12mo. Return edge Q5−Q1 ≈ **+2.5%/quarter, durable ~6mo then dies/reverses** past 9mo. |
| 15 | "Build it: (a) slope-momentum book — does *acceleration* beat the level filter? (b) slope-rollover as the *exit* the champion lacked." | Long-only top-slope (quintile & top-80, quarterly reform) + champion with slope-rollover exit overlay, vs champion/control/Nifty | **Champion wins both; exit hypothesis refuted.** Slope top-80 = 21.5%/1.06/−31% — beats control (real edge, confirms #14) but **loses to champion 23.8%/1.14/−26%** on all 3 axes (slope is correlated with the stack, minus its risk control). Exit overlay = 22.6%/1.13/−26.6% — slightly *worse*: rollover is a *late* exit; the champion's score-crack exit fires earlier. Champion robust to both entry & exit challenges. |
| 18 | "Same strategy, PIT-true vs PIT-false — is the no-PIT spike real or survivorship?" | Aligned-stack + EW-all control, each run with members_as_of (PIT-ON) vs today's N500 applied backward (PIT-OFF) | **Pure survivorship — proven.** PIT-OFF inflates CAGR by **~+10pts** (aligned-stack 36% vs 26.3% in 2020–26; 29% vs 18.7% in 2023–26). The **identical +10pt spike appears on EW-all with no strategy at all** → the bias is in the *universe* (holding today's survivors backward), not the strategy. The strategy's edge *over its own universe* (~+6pts) survives both modes → genuine alpha; only the headline is inflated. Engine PIT validated correct. **Honest number = PIT-ON 26.3%.** *Source of the excess (decomposed, `pit_excess_source.py`): (a) ~52 sub-index "risers" with pre-2020 data, held during their climb INTO N500 — 14% of book but 72.9% CAGR vs incumbents' 31.2%; (b) silently dropping the ~255 later-demoted/delisted laggards. NOT IPOs (112 of 164 entrants had no early data → never selectable, confirming new listings contribute nothing).* |
| 19 | "Build the honest broad universe: capture Microcap250 PIT, load prices, compute indicators, run aligned-stack on N500∪Microcap250 PIT — is the riser edge REAL or just the survivorship mirage?" | Captured Microcap250 reconstitution (11 reviews, urllib+pypdf); loaded 594/631 ever-members (anonymous tvfeed; 37 delisted residual); computed 304 indicators; ran broad PIT aligned-stack | **Riser edge is REAL but ~⅓ the illusion.** Broad PIT aligned-stack: **21.9% / 1.11 / −24.6%** vs N500-champ 19.0% / 1.02 (2022-06→26); +2.9–3.2pts CAGR, +0.1 Sharpe, same DD. Decomposes: +1.5pts universe (PIT microcap risers) + ~2.9pts gate. **`0` eligible names skipped for missing price** → the strength gate structurally avoids the dead/delisted, so the data gap doesn't bias it (integrity closed, measured). vs the PIT-OFF survivor illusion of ~+10pts: **~⅓ real, ~⅔ hindsight.** Caveats: microcap liquidity caps deployable capital; 2022+ only, small/midcap-bull-flattered. |
| 16 | "Stop generating — is the champion's 23.8% steady across regimes, or carried by a couple of years?" | Per-calendar-year + first-half/second-half breakdown of champion vs EW-all control, % months beating control | **Edge is front-loaded & decaying — headline corrected.** Beat control 6/8 years but **lost 2023 & 2025 (both recent)**. H1 (2019–22): spread **+78pts**, Sharpe **1.29 vs 0.80**. H2 (2022–26): spread **+7.7pts**, Sharpe **0.98 vs 0.97 (parity!)**. **2021 alone = +28pts spread > the entire H2.** It's a *narrow-momentum* bet (wins when leadership narrow, lags broad rallies). Only drawdown edge persists (~−25% vs ~−35% both halves). Honest forward expectation ≈ H2, NOT 23.8%. |
| 17 | "Walk-forward the champion — are the 50/60/80/90 thresholds robust or fitted? Does the edge survive OOS?" | Rolling 24m-train/12m-test folds; pick best threshold config by train Sharpe, apply OOS; vs fixed champion vs control | **Two wins, two tempers.** OOS 2021–26 (65mo): **fixed champion 24.6%/1.28 BEATS control 19.2%/1.10** (+5.4pts) — edge is real OOS. **Tuning adds nothing** (24.7%/1.26) and the selected config is unstable (4 distinct/6 folds) → **thresholds are interchangeable, NOT overfit** (structure carries it, overfit worry resolved). BUT lumpy/regime-contingent (per-fold Sharpe 4.67→−0.26, carried by 2021 & 2024, loses 2023 & 2025), and **OOS drawdown is TIED with control (−24 vs −24)** — the earlier DD edge was a 2020-crash artifact. |

---

## 3 · Scoreboard (net, 2019–2026, identical dates)

| Strategy | Avg names | CAGR | Sharpe | maxDD | vs control |
|---|--:|--:|--:|--:|:--|
| **Aligned-stack, hold-all** (gate #9) | 79 | **23.8%** | **1.14** | **−26.1%** | **+6.5 ✓✓** best all 3 axes |
| `mean_long` leaders (gate #6) | 210 | 20.8% | 1.01 | −34.4% | +3.5 ✓ |
| short×mid-cross, top-zone (gate #11) | 25 | 19.7% | 0.93 | −29.2% | +2.4 ✓ (breadth-capped) |
| **EW-all-N500 (the honest control)** | ~480 | 17.3% | 0.85 | −35.6% | — benchmark |
| `mean_short` fast (gate #5) | 244 | 16.3% | 0.83 | −34.4% | −1.0 (wash) |
| buy@10 turnaround (gate #7) | 164 | 11.9% | 0.62 | −41.5% | −5.4 ✗ |
| Nifty 50 (cap-weight index) | 50 | 10.8% | 0.68 | −32.2% | (not the right benchmark) |

**Concentration sweep (within aligned-stack, ranked by 66d ROC):**

| Book | Avg N | CAGR | Sharpe | maxDD |
|---|--:|--:|--:|--:|
| top-10 | 10 | 12.7% | 0.57 | −39% |
| top-20 | 20 | 18.4% | 0.79 | −31% |
| top-30 | 30 | 21.2% | 0.93 | −30% |
| **hold-all** | 79 | **23.8%** | **1.14** | **−26%** |

Every step of concentration *drops* CAGR and Sharpe and *deepens* drawdown. The optimum is to
hold the whole qualifying set.

---

## 4 · Principles that survived

**◆ RS is a gate, not a ranker.** Use it to define *who is eligible*, then hold them broadly.
Every attempt to *rank within* the eligible set (by ROC #10, by velocity #2–3, by `mean_long`
level #8) lost. The filter is the alpha; the ranking is noise that sacrifices breadth.

**◆ IR ≈ IC × √breadth (Fundamental Law of Active Management).** The aligned-stack wins because
it maximizes the *product*: the strongest filter (multi-timeframe confirmation = high IC) held
at adequate breadth (~79, √79 ≈ 8.9). Over-narrow it and √breadth bites (#10, #11); weaken the
gate and IC bites (#5). At ~25–30 names you get ~0.93 Sharpe almost regardless of selection
method — **breadth dominates once the gate is decent.**

**◆ "15–20%" is an output, not a target.** RS-as-filter works across a wide band (16% at #9,
42% at #6). What made ~16% *best* is not the number — it is that the gate's pass-count **floats
27→172 with the regime** and auto-shrinks in bad tape. That conditional de-risking is what
produced the −26% drawdown (best of all). Hard-coding "always hold 80" discards it.

**◆ Breadth-as-thermometer (the under-exploited idea).** Standard practice ranks by RS and
holds a fixed K, throwing away *how many* names qualify. A conditional gate keeps that number —
when few names clear a strong multi-timeframe stack, the market is telling you to be small.
Most of the risk-adjusted edge came from this sizing signal, not from stock picking.

**◆ Direction matters: strength pays, velocity/contrarian doesn't.** Durable, slow,
multi-timeframe strength (#6, #9) is the edge. The fast/velocity slice is dead (t ≈ −0.91, #3),
and buying weakness/turnarounds is actively value-destructive (#7). "RS" generically is too
loose — specify the strength axis.

**◆ Exit speed helps in crashes only (corrected by exp #17).** The strict score>90 / short>80
thresholds force a fast exit at the first crack in momentum, which gave a best-in-class −26%
drawdown over the *full* period. BUT the walk-forward showed this **does not persist**: OOS
2021–26 the champion's maxDD *ties* the control (−24 vs −24). The drawdown edge was essentially
a **2020-COVID-crash artifact** (fast exit paid in that one crash). *Self-correction: an earlier
draft of this log called the drawdown the "most trustworthy part" — the walk-forward refuted
that.* No reliable drawdown protection outside sharp crashes.

**◆ The RS trend has a tradeable life-cycle (exp #14).** `mean_long` *acceleration* (`ml_slope`)
is persistent, not memoryless: rank-IC 0.50 over the next quarter, fading to 0.18 by 6m and
*reversing* to −0.41 by 12m. A rising trend lives a median ~5 months, its continuation hazard
*rises* mid-life (.90–.92 at months 6–9 — the strong get stronger), then rolls over at ~10–12m.
The payoff to sorting on acceleration is ~+2.5%/quarter, durable ~6 months, then it dies and
faintly reverses. **Tradeable rule: enter on positive/rising slope → hold ~2 quarters → exit on
rollover, never past ~9 months.** Crucially, **slope rollover is the principled exit the
aligned-stack champion lacked** — the champion's edge was its early de-risking, and slope makes
that mechanistic. Slope is both a standalone entry alpha and an exit overlay.
*(Update from exp #15: tested — the champion **dominates** both. As an entry, slope (21.5%/1.06)
beats the control but loses to the level stack on all 3 axes; as an exit, slope-rollover is
*later* than the champion's score-crack exit and slightly hurts. Slope is a real but correlated
edge that the aligned-stack already subsumes, with better risk control. The "missing exit"
hypothesis is wrong — the champion's fast exit is already superior.)*

**◆ The RS "breath" is a symptom, not a signal (exp #12–13).** The fast oscillation of
`mean_short` around `mean_long` does *not* mean-revert in price terms at a 3-month horizon —
deep dips predict the *lowest* forward return, extended states the *highest* (momentum, not
reversion). Winners breathe ~12× only because they trend long enough to oscillate that many
times; the breath-count is downstream of a durable trend, not a cause you can time. The firm
cross-sectional predictor is **trend slope** (`mean_long` acceleration, t=2.31), with level
marginal. **First-draft equation:** `E[fwd 3m %] ≈ 7.2 + 0.74·z(ml) + 0.71·z(ml_slope)` — the
oscillator terms vanish. This kills the "buy-the-inhale" / mean-reversion build and redirects
the model toward the trend as a *dynamical state* (level + slope).

**H=1 confirmation (escape hatch closed):** reran the surface + FM at a 1-month horizon. No
reversion in tradeable regimes — extended still ≥ inhale in the 65–80 and >80 bands; the only
faint inhale-edge sits in `ml<50` (falling-knife zone, already shown value-destructive in #7).
"Buy the recovery" *reverses* at 1m (still-falling 2.1% > turned-up 1.4%). And **every FM
coefficient is insignificant at 1m** (slope t=0.88 vs 2.31 at 3m) → the signal is *slow*: trend
slope needs ~3 months to express, the oscillator is noise at both horizons. **Natural cadence
is quarterly, not monthly.** Mean-reversion of the breath is closed for good.

---

## 5 · Caveats (honest bias log)

- **In-sample.** All results are 2019–2026 full-period. The champion is the strongest result,
  which means it most needs out-of-sample proof.
- **Multiple testing.** ~8 distinct variants were tried on one dataset → the "winner" is partly
  luck. Each additional in-sample test inflates this.
- **Threshold overfit surface — RESOLVED (exp #17).** The four cuts (50/60/80/90) were the
  obvious overfit worry. Walk-forward selecting thresholds on past data added *nothing* over the
  fixed cuts (24.7% vs 24.6%), and the selected config was unstable across folds. The thresholds
  are **interchangeable, not fitted** — the edge is in the multi-timeframe structure, not the
  numbers.
- **Two predictions I got wrong** (recorded to keep the log honest): (a) I predicted the score
  would pile at 50 — it piles at 80–100; (b) I predicted the aligned-stack would lag from
  over-extension — it won outright.
- **Known bug:** the in-process simulator reports `sharpe=inf` on some per-window runs; the
  walk-forward (#4) sidestepped it by selecting on CAGR. Fix pending.

---

## 6 · Open questions / next

1. **Consistency check — DONE (#16): edge is front-loaded & decaying.** The champion's full-period
   23.8% is carried by 2020–21; the recent regime (2022–26) is near-parity with the universe on
   risk-adjusted terms. Headline corrected to the H2 expectation.
2. **Walk-forward — DONE (#17): edge real OOS (+5.4pts/Sharpe 1.28 vs 1.10), thresholds robust,
   but lumpy.** Genuine OOS edge over 2021–26, interchangeable thresholds, no persistent DD edge.
3. **Regime conditioning — now THE lead.** The champion is a narrow-momentum bet that *wins
   2021/2024, loses 2023/2025*. If the regime is detectable in advance (qualifying-count /
   breadth thermometer, or market-breadth), sitting out broad-rally regimes could turn the lumpy
   edge into a steadier one. This is the single highest-value remaining test — and it builds on
   the one mechanism (breadth-as-thermometer) that survived everything.
3. **Validate the champion OOS** — per-year and first-half/second-half consistency of the
   aligned-stack vs EW-all-N500. Is 23.8% steady, or carried by 2021/2023?
4. **Prove breadth-as-thermometer** — plot qualifying-count vs forward market return; confirm
   the pass-rate is a regime signal, not an artifact.
5. **Walk-forward the gate thresholds** — are 50/60/80/90 robust, or did those exact cuts get
   lucky?
6. **Fix the sim `sharpe=inf` bug** at the source.

---

## 7 · Swing-horizon sweep (#20): `mean_mid` is the swing workhorse; a dumb gate beats clever ranking *(2026-07-03)*

**Question.** Is there a real 5–20-day swing edge in the TrueVX metrics, under strict IS/OOS
discipline? Panel: 2.05M rows (1,282 symbols × 2018→2026-06), **PIT N500 members only**,
market-relative forward returns, **net 30 bps round-trip** (more conservative than the 22 bps
used elsewhere in this log). Discovery locked to **IS 2018-07→2022-12**; **OOS 2023-01→2026-06
touched exactly once**, configs frozen beforehand.

**Rank-IC sweep (daily cross-section vs xr5/xr10/xr20).** Two opposite, robust effects:
- *Levels pay*: `mean_mid` is the best single signal at every horizon (IC +0.025→+0.039,
  non-overlap t up to 4.2), then `mean_short`, `mean_long`. Positive 8 of 9 years.
- *5-day changes anti-predict*: `roc5` IC −0.037 (t −7.9), `d5_score` −0.028 (t −8.2),
  `score−mean_short` gap −0.023 — negative **every** year 2018–2026.

**Double-sorts (IS).** The reversal is mostly "don't chase spikes in weak names" (low-`mm` ×
high-`roc5`: −40 bps/10d). In the top `mm` quintile the level dominates every change-column
(+40…+55 bps/10d vs +14 universe); the best corner is a *mild dip* (+52/+55). Top-`mm` decile
≈ the aligned-stack cell (+49 vs +46 bps/10d) with one condition instead of four. Plain
`roc20` top-20 at 10d hold is garbage net of costs (+3.5% CAGR, Sharpe 0.01) — churn eats it;
`mm`'s 66-day smoothing keeps turnover ~1.4%/day.

**Ladder sims (top-K daily refresh, 10-day ladder, N500).** IS winner: top50 `mm`
(26.8% CAGR / Sharpe 1.14 / alpha +13.7%). OOS: alpha compressed to +2.9%, and 2025 ran it
over (−11.9% vs bench +6.4%) — the naked level book is regime-fragile, echoing #16/#17.
Two add-ons, each frozen pre-OOS, each helped OOS independently: a 25% dip tilt
(`0.75·rank(mm) + 0.25·rank(−d5_score)`) and a classic untuned 200d-SMA gate on Nifty 500.

**Final composite — top50 combo · 10d ladder · 200d gate (net 30 bps):**

| period | CAGR | bench | alpha | Sharpe | maxDD |
|---|---|---|---|---|---|
| IS 2018-07→2022-12 | +20.3% | +13.1% | **+7.2%** | 1.14 | −17.5% |
| OOS 2023-01→2026-06 | +17.8% | +12.5% | **+5.3%** | 0.98 | −12.0% |
| Full 2018-07→2026-06 | +18.8% | +12.8% | +5.9% | 1.03 | −18.0% |

Alpha consistent on both sides of the split — the hallmark of a real (moderate) edge. 2025
tamed from −11.9% to +2.1%. An internal-breadth gate (frac `mm`>50 ≥ 0.4) tried as an OOS
diagnostic is **worse** than the price gate (Sharpe 0.76, DD −25%) — dropped.

**Principles that survived.**
1. *Where RS has been* (levels, esp. `mean_mid`) predicts; *how RS just moved* (5d deltas)
   anti-predicts. Buy strength; never chase a 5-day spike.
2. The swing edge is the position edge sampled faster: `mean_mid` turns over slowly enough to
   survive costs; raw 5–20d momentum ranks do not.
3. Naked RS-level books die in regime breaks (2022, 2025). One dumb, untuned trend gate was
   worth more OOS than any ranking refinement (Sharpe 0.62 → 1.02).
4. Concentration beyond ~top-50 hurt OOS (top20 alpha −1.2%) — breadth is part of the edge.

*Bench is the Nifty 500 price index (no dividends), so alphas are ~1–1.3 pts flattered vs TRI.
Reproduce: `scripts/research_swing/` — build_panel → ic_analysis → double_sort → topk_sim →
oos_validate → final_composite.*

---

## 8 · "Alpha" decomposition (#21): gate premium + breadth-scaled selection; offset tranches are free diversification *(2026-07-04)*

**Question.** Why does the saved simulator strategy **Alpha** (gate `truevx_score>90 ∧ mean_short>80 ∧
mean_mid>60 ∧ mean_long>50` → rank eligibles by `20_day_return` → top-25 EW, monthly **mid**,
PIT **NIFTY500MICRO750**, statutory charges on) win, and why does it scale with universe breadth?
All engine runs (`/api/simulation/run`) 2020-01→2026-07 vs Nifty 50 (+99.2% ≈ 11.3% CAGR).

**Decomposition (monthly mid-month cross-sections, fwd 1-mo returns, PIT N750):**

| layer | fwd/mo | increment | t |
|---|---|---|---|
| universe EW (~722 names) | +1.98% | — | — |
| gate survivors (~122 avg) | +2.58% | **+0.59% gate premium** | 2.24 |
| top-25 by mom20 | +3.22% | **+0.64% selection premium** | 1.90 |

Gross ≈ 46%/yr → engine net **36.9% CAGR / Sharpe 1.39 / maxDD −35.6%**. The gate premium is a
*per-name* property (+0.55…+0.59%/mo in every universe tested); the selection premium is not —
it scales with the pool.

**Breadth (engine, identical config, only universe changed).** Selection spread ≈ ρ·σ·λ(k/n),
λ = mean z of the upper k/n tail — grows with pool size n and cross-sectional dispersion σ:

| universe | CAGR | Sharpe | maxDD | eligibles avg | ranker spread | disp σ/mo |
|---|---|---|---|---|---|---|
| N500∪Micro250 | **36.9%** | 1.39 | −35.6% | 122 (top 20%) | +0.64%/mo (t 1.9) | 10.6% |
| NIFTY500 | 30.4% | 1.26 | −35.5% | 85 (top 29%) | +0.27%/mo (t 1.0) | 9.8% |
| NIFTY200 | 29.0% | 1.38 | −29.8% | 36 — 26% of months have <25 eligibles | — | 8.6% |

Micro-only names (in 750, not N500) average **9.6 of the 25** slots and earn **+4.09%/mo vs
+2.45%** for N500 names; 9.6/25 × 1.64 ≈ +0.63%/mo — the *entire* N750-vs-N500 gap. The
universe upgrade **is** the micro sleeve.

**Tranches (engine, only rebalance day changed).** mid 36.9%/1.39/−35.6 · first
36.9%/1.38/−36.7 · **50/50 combo 36.9% / Sharpe 1.41 / vol 25.3→24.7%**. The two books share
only **23%** of names (union ≈ 41) yet correlate **0.90 daily** — same factor, different
stocks: σ_combo = σ·√((1+ρ)/2). Splitting halves name-specific risk and rebalance-timing luck
(var ∝ 1/K) at zero return cost; it does **not** hedge regime risk (maxDD unchanged).

**Concentration.** top-25 36.9% > top-50 34.6% > all-eligibles(122) 31.5% CAGR; Sharpe flat at
1.44 for 50–122 names. Two-tranche 25+25 ≈ 41 names lands near-max CAGR *and* near-max Sharpe.
Contrast #10/#20: on this tight 4-condition gate at **monthly** cadence the mom20 ranker *adds*
+0.64%/mo; at 10-day cadence the same signal died to churn (84%/mo name turnover here costs
~0.2%/mo statutory ≪ gross spread). Cadence matched to signal half-life — not the ranker — was
the difference.

**Caveats.** No impact model: 84%/mo turnover with ~40% of book in microcaps is fine at ₹1L,
capacity-bound at scale. 2020–26 is a historic micro bull (universe EW alone +1.98%/mo) — part
of the level is beta. Trend-following gate → expect the −36% DD to recur (cf. #16/#17).
Selection-premium t = 1.90, not bulletproof; gate premium (t 2.24) and the micro decomposition
are the firmest legs. Micro-only universe alone: 38.0% CAGR but −40.3% DD and worst liquidity —
N750 is the better risk/capacity trade.

*Reproduce: `scripts/research_alpha/` — `run_variants.py` (engine sweep) → `mechanism.py`
(cross-sections) → `timing.py` (tranches) → `curves.py` (stats/combo).*

---

## 9 · Execution realism (#22): slippage sensitivity + staggered fills *(2026-07-05)*

**Question.** The engine fills everything at the same-day close, frictionless beyond statutory
charges. What do realistic execution costs do to the Alpha-family results, and is spreading
fills over several days (to slice illiquid microcap orders) costly?

**Slippage sensitivity (engine, `slippage_bps` param: buys close×(1+s), sells close×(1−s),
applied to the engine's modeled trades incl. the full re-true — slightly conservative).**
Arpi's 4-sleeve portfolio, 2022-01→2026-07, all else identical:

| bps/side | total return | alpha | Sharpe | maxDD |
|---|---|---|---|---|
| 0 | +209.7% | +172.0 | 1.12 | −23.7% |
| 15 | +185.2% | +147.5 | 1.02 | −24.2% |
| 25 | +169.9% | +132.2 | 0.96 | −24.6% |
| 40 | +148.6% | +110.9 | 0.86 | −25.1% |

Drag ≈ **1 pt CAGR per 10 bps per side** at this turnover (~84%/mo of the book). The verdict
never flips: even at a harsh 40 bps the alpha is +111 pts. Honest planning baseline: **25 bps
→ ~24–25% CAGR**, not the frictionless ~28.6%. Drawdown barely moves — slippage is a steady
tax, not a risk amplifier. maxDD −23.7→−25.1 over the whole sweep.

**Staggered execution (panel replication, selection FROZEN at rebalance day T, fills at close
of T+k; Alpha config, PIT N750, 55 monthly-mid rebalances 2022→2026, frictionless):**

| policy | CAGR | Sharpe | maxDD |
|---|---|---|---|
| all at T+0 (sim assumption) | 31.97% | 1.30 | −23.9% |
| all at T+1 / T+2 / T+3 / T+4 | 33.5 / 31.7 / 35.2 / 32.2% | 1.29–1.40 | −24.8…−27.7% |
| **⅕ each day T…T+4** | **32.94%** | **1.33** | −25.9% |

- Basket entry drift vs decision-day close: **+0.06…+0.10% mean** per rebalance (std 1–2.8%)
  — the "momentum names run away if you're late" fear is not supported here.
- Single-day execution is a timing lottery: same decisions, different fill day → **31.7–35.2%
  CAGR (3.5 pts of pure execution-day luck)**. Staggering averages the draw away; monthly vol
  unchanged (6.59→6.62%).

**Execution policy adopted for live (Stage 2).** Decision fixed at T per the sim; execute
~⅕ of the plan per closing window over T…T+4, SELLs leading BUYs each day, LIMIT orders
(band ~1%), ADV-capped slices for microcaps. Do **not** re-rank mid-stagger — that's a
different, untested strategy. The sim's same-day-close model remains the correct expectation;
staggering removes its variance.

**Caveats.** Slippage is flat per side (no size/impact curve — refit from realized fills once
live reconciliation exists); stagger test is frictionless and single-config; the engine's
full-re-true accounting overstates continuing-position costs slightly (true numbers a shade
better); short path takes no slippage (long-only tested).

*Reproduce: `scripts/research_alpha/slippage_sweep.py` (engine sweep, in-process) and
`scripts/research_alpha/execution_stagger.py` (T+k books + staggered average).*

---

## Appendix · Scripts

| Experiment | Script |
|---|---|
| Cluster discovery / regime map (#1) | `scripts/discover_indicator_clusters.py`, `cluster_structure_probe.py`, `render_regime_map.py` |
| Velocity refutation (#2, #3) | `scripts/regime_forward_returns.py`, `velocity_factor_test.py` |
| Walk-forward validator (#4) | `walkforward/` package, `run_walkforward.py`, `scripts/wf_trim_run.py` |
| `mean_short` zone-cross (#5) | `scripts/shortmean_zone_strategy.py` |
| `mean_long` zone-cross (#6) + control | `scripts/meanzone_compare.py` |
| buy@10 turnaround (#7) | `scripts/meanshort_10_90_strategy.py` |
| multi-peeling terciles (#8) | `scripts/meanlong_metrics_tiers.py` |
| **aligned-stack (#9, champion)** | `scripts/aligned_stack_strategy.py` |
| concentration sweep (#10) | `scripts/aligned_stack_topn.py` |
| short×mid-cross (#11) | `scripts/short_mid_cross_top.py` |
| winner autopsy / signature (#12) | `scripts/winner_signature_probe.py` |
| RS-wave → price model (#13) | `scripts/rs_wave_model.py` |
| `ml_slope` persistence (#14) | `scripts/rs_slope_persistence.py` |
| slope-momentum + exit overlay (#15) | `scripts/slope_momentum_strategy.py` |
| champion consistency / regime breakdown (#16) | `scripts/aligned_stack_consistency.py` |
| walk-forward champion / threshold robustness (#17) | `scripts/walkforward_champion.py` |
| PIT vs non-PIT survivorship (#18) | `scripts/pit_vs_nonpit.py` |
| data-integrity / coverage audit | `scripts/data_integrity_audit.py` |
| where PIT-off excess comes from (#18b) | `scripts/pit_excess_source.py` |
| riser origin (below-N500 tracing) | `scripts/riser_origin.py` |
| Microcap250 reconstitution capture (#19) | `scripts/parse_pr_microcap.py`, `capture_microcap_reconstitution.py` |
| Microcap price load (#19) | `scripts/microcap_load_batch.py`, `microcap_fix_and_load.py`, `microcap_feasibility.py` |
| broad-universe PIT backtest (#19) | `scripts/broad_universe_pit.py` |
| swing-horizon IC/double-sort/ladder + OOS (#20) | `scripts/research_swing/` (6 scripts, run in order) |
| **"Alpha" decomposition (#21)** | `scripts/research_alpha/run_variants.py`, `mechanism.py`, `timing.py`, `curves.py` |
| **Execution realism (#22)** | `scripts/research_alpha/slippage_sweep.py`, `execution_stagger.py` |

## 2026-07-11 — mean_long living shape (full universe)

Characterized mean_long (SMA222 of truevx_score, relative to Nifty 50) across 1177 stocks
2018→2026-07: velocity capped at 0.45/day (observed p99 0.40, median 0.118); occupancy
humped 30–80 with extremes rare; phases median 60d/±4 amp (p90 235d/±38); 73.8% of stocks
never complete a 20→80 trip. Price LEADS the indicator ~21d (peak corr 0.36 at lag −21;
+21d onward ≈0.03). Concurrent rel return during rising phases is NEGATIVE (−2.2% median)
— pure lag artifact. Forward map: direction beats level everywhere (rising vs falling ≈
+2–3% rel/63d spread); best state = fresh 40↑/50↑ cross (+6–8% rel/252d, 72% abs hit);
20↑ bounces are short-horizon traps (−3.5% rel/63d); any falling state is persistently
forward-negative (−4 to −7% rel/252d). Full report: scripts/research_meanlong/MEANLONG_SHAPE.md;
script + JSON alongside. Caveat: computed-universe survivorship inflates abs returns;
rel columns are the robust ones.
