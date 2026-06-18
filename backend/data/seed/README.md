# Seed Data — DRAFT, NOT SHIP-READY

These files are committed as a structural draft only. Before any live backtest:

1. **`nifty50_changelog.csv`**: Every row with `source_url=VERIFY` must be verified against the authoritative NeuString press releases at https://www.niftyindices.com/ (index announcements / press releases section). Replace each `VERIFY` token with the exact press-release URL. Company names in `added`/`removed` columns must then be mapped to their exact tvdatafeed NSE trading symbols (e.g. `TATACONSUM`, `APOLLOHOSP`, `ADANIENT`, `LTIM`, `SHRIRAMFIN`, `BEL`, `TRENT`, `DIVISLAB`, `JIOFIN`, `ETERNAL`, `INDIGO`, `MAXHEALTH`). Do not guess symbols — derive from the official NSE scrip list.

2. **`nifty50_current.csv`**: Currently empty (header only). Populate by downloading the authoritative current constituents from `https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv`, reducing to a single `symbol` column of tvdatafeed NSE trading symbols. Do not hand-type the 50 tickers.

Until these steps are complete, `load_provider_from_seed` will return an empty universe for NIFTY50 and backtests will produce incorrect results.
