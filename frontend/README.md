# Fund Desk (tvagent)

A trading desk for the five-sleeve model portfolio. The market-hunt backend computes the model, and you trade the
weekly ticket by hand. There is no broker connection.

Spec: `../docs/specs/2026-09-27-fund-trading-desk-design.md`

## Run it

```bash
# 1. backend (market-hunt, port 3001) — needs MongoDB running with market data
cd ../market-hunt
.venv/Scripts/python.exe api_server.py

# 2. frontend (port 3300), production mode
cd ../frontend
npm install
npm run build
npm start                # http://localhost:3300
```

- The frontend proxies `/api/*` to `API_PROXY_TARGET` (default `http://127.0.0.1:3001`).
- The fund API is localhost-only unless `MH_API_TOKEN` is set.
- Serve production builds. Do not run `npm run build` while `next dev` is running, because it corrupts `.next`.

## Weekly routine

1. **After the close on your trading day**, refresh market data (`market-hunt/scripts/refresh_all.py`).
2. **Desk → Compute this week's run.** This takes about a minute and loads all five sleeves.
3. **Run review.** Check the pre-trade checks. A `BLOCK` prevents publishing; a `WARN` is information. Then read the ticket.
4. **Publish.** This freezes the ticket as the week's trades.
5. **Trade at or after the next open.** Sells first, then buys, using the limit guidance.
6. **Ticket page → record executions:** done, partial or skipped, with your fill price.
7. **Tracking** shows the gap between your recorded positions and the model.

- **First run of a model version** is an *initial build*: it buys the whole model book at target weights, sized to
  **Capital**.
- **Changing settings.** Changing capital, model start, sleeves or costs in **Settings** starts a new model version.
  Set Capital once, before go-live. After that, **never change Capital to add or remove money** — it would trigger a
  new initial build.
- **Adding or withdrawing money after go-live:** record it on the **Cash** page. Then compute and publish a run; its
  ticket spreads a deposit across the sleeves at target weights, or sells a withdrawal pro-rata.

## Tests

```bash
npm test                 # vitest
npx tsc --noEmit         # types
```
