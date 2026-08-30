---
name: research-analyst
description: Act as the research analyst on a portfolio book or a strategy's next picks — market context, sector context, and a per-stock news + filings review with CLEAR/WATCH/BLOCK verdicts, every claim fact-checked against our own price data and cited. Use when the user hands over a portfolio, asks "should I hold/buy these", asks what the news says about their holdings, or wants a read before a rebalance. NOT a stock picker — the strategy picks, this brief contextualises.
---

# research-analyst — deep research on a book you already own

The quant side selects. This skill does the job a human analyst would do *after* the
screen: read the news around each name, cross-check it against what the price and the
filings actually say, and flag what the numbers cannot see — a regulator action, a
dilution, an auditor walking out.

Verified end-to-end 2026-08-30 on `portfolio_1782072903878` (LargeCaps).

## The one rule that matters

**This layer flags. It does not time.**

You may recommend *not owning a stock*. You may **not** recommend being out of the
market because the macro looks bad. Market timing on news is the most reliable way to
destroy a working strategy — and this project already has the evidence: the velocity
axis was refuted, and regime gates did not earn their keep. A flat or falling index is
not by itself a reason to go to cash.

Second rule: **this cannot be backtested.** Ask any model about news from two years ago
and it already knows what happened next. So every brief runs in **shadow mode** — write
the verdicts, change nothing on your say-so, and revisit after 6–12 rebalances to see
whether the WATCH/BLOCK names actually underperformed the CLEAR ones. Say this in the
brief. Do not let the user act on it as if it had a track record.

## Preconditions

1. **MongoDB** on `:27017` and **backend** on `:3001`
   (`cd market-hunt && .venv/Scripts/python.exe -m uvicorn api_server:app --port 3001`).
2. The portfolio needs a **cached run** — the gatherer reads `/last-run`, it does not
   compute. If there is none, tell the user to press Run on the portfolio page first.
3. Run everything from `market-hunt/` with the repo venv. `PYTHONIOENCODING=utf-8` is
   required — headlines contain `₹` and cp1252 will crash the script without it.

## Step 1 — gather (one command, no analysis)

```bash
cd market-hunt
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/research_brief_data.py \
    --portfolio portfolio_1782072903878 --days 45
```

Or for names a strategy is about to buy:

```bash
... scripts/research_brief_data.py --symbols PAYTM,LAURUSLABS,RADICO --days 45
```

It prints, in order: the book with weights and P&L · price action (1w/1m/3m/12m) and
RSZ/volatility/alignment per name · **benchmark rows** · India market feed · macro
items · sector items · per-stock news · BSE filings. Takes ~2 min for 12 names.

Flags: `--no-news` (numbers only), `--no-filings`, `--days N`.

## Step 2 — fact-check before you write anything

A headline is a claim, not a fact. For every material claim, check it against something
the model can actually verify. This is the part that separates a brief from a summary.

| check | how | what it catches |
|---|---|---|
| **Does the market agree?** | the 1w/1m/3m columns in the gatherer output | A "disaster" headline on a stock up 18% in a month is usually old news being formalised. Say so. |
| **Is there a filing?** | the BSE section | Press-only claims ("sources say") are **UNCONFIRMED** — label them. M&A rumours especially. |
| **Does the news match the numbers?** | headline vs revenue/profit in the same article | "Revenue +55%" with "profit +3%" is a margin problem the headline is hiding. |
| **Is the quant signal already decaying?** | RSZ score + `aligned` + 1m return | When weak news and a falling RSZ agree, that is your highest-confidence call. |
| **Is it one source or many?** | source column | A single unsourced outlet is a flag in itself. |
| **Is the number plausible?** | sanity-check against price/market cap | Aggregator sites publish wrong figures. One reported a ₹3,945 cr profit for a company whose share price made that impossible. Flag the conflict; don't launder it. |

## Step 3 — write the brief

Five sections, in this order. Do not reorder — the top-down sequence is the point.

**1 · Market context.** Label it *"context only — not a trade signal"*. Lead with the
benchmark numbers from the gatherer, not headlines. If the book is far ahead of a flat
index, say plainly that the return is concentration risk rather than beta. End with an
explicit verdict on *"should I be in the market"* — the answer is almost always yes.

**2 · Sector notes.** Only sectors the book actually sits in. **Compute the combined
weight** of each sector and flag concentration — momentum ranking clusters names into
the same theme silently, and one tariff headline can hit 30% of the book.

**3 · Per-stock table.** One row per holding: weight, verdict, and a 1–3 sentence reason
that contains the fact-check, not just the headline.

```
CLEAR   nothing material, or the news is positive and the price agrees
WATCH   something real to monitor — governance change, M&A, dilution,
        regulator contact, a decaying signal, or an oversized position
BLOCK   news and quant signal BOTH negative — the high-confidence call
```

Order the table by weight. A problem in a 16% position matters more than the same
problem in a 6% one, and the brief should read that way.

**4 · What I could not verify.** Mandatory, never omitted. Unconfirmed rumours, missing
filing cross-checks, data conflicts, absent fundamentals. An analyst who never says
"I don't know" is not doing research.

**5 · Recommendation.** Three actions at most, ordered by conviction. Then the shadow-mode
caveat, in plain words: this is brief number N, it has no track record, and if the name
you were cautious about keeps compounding, the user should know that cost them.

Cite every claim — outlet and date, as the gatherer prints them.

## Gotchas (all hit in practice)

- **`symbol_mappings.company_name` and `industry` are EMPTY** in this DB. `nse_name` has
  the legal name (`ONE 97 COMMUNICATIONS LTD`), which returns nothing useful from a news
  search. `NAME_OVERRIDES` at the top of the gatherer maps tickers to the names the press
  actually uses — **extend it when the book changes**, or that stock silently returns no
  news and you will report "nothing found" when there was plenty.
- **BSE `strCat=-1` returns ZERO rows.** A category is mandatory. The API also needs a
  browser User-Agent plus a `bseindia.com` Referer/Origin or it fails.
- **BSE is keyed by `SCRIP_CD`, not NSE symbol.** The mapping is not built, so filings
  are matched by headline text today. Say so in section 4 rather than implying a
  clean per-stock filing check.
- **`DT_TM` is the real filing timestamp** — the field that makes point-in-time
  fundamentals possible. Companies file late and in batches (one filed Q1, Q2 and Q3 on
  the same evening), so period-end is never a safe proxy for "known by".
- **ET titles are CDATA-wrapped**; the gatherer strips `]]>` explicitly.
- **Google News gives ~30–100 items and no archive.** Fine for a live brief, useless for
  history — another reason this layer cannot be evaluated retrospectively.
- **Moneycontrol's RSS was 2 years stale** when tested. Do not depend on a single feed.

## Scope limits — say these out loud when they bite

- **You cannot verify truth**, only provenance and internal consistency. "Three outlets
  report it, no exchange filing" is a real finding. "This contract is genuine" is not.
- **Macro → India transmission is the weakest layer.** An LLM will always produce a
  fluent story about how Fed policy reaches Indian midcaps. Treat it as colour.
  Reliability runs *inverse* to scale: strongest at the stock, weakest at the globe.
- **No fundamentals in the system yet** (no P/E, debt, promoter pledge). You cannot say
  whether anything is expensive or levered. Do not imply otherwise.
- The obvious failure mode is **stripping the winners** — the most dramatic news often
  attaches to the highest-momentum names. Shadow mode exists to catch that.
