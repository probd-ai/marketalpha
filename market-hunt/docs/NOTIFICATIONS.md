# Rebalance Notifications (Stage 1) — Telegram

Read-only: computes probable moves from the latest loaded data and messages you.
No broker calls, no orders.

## What you get

- **T−2 preview** (2 business days before each sleeve's estimated due date):
  probable entries/exits with momentum scores — plan ahead.
- **Day-of order list** (on the due date): 🔴 SELL exits (full), 🟢 BUY adds with
  ESTIMATED quantities (equal-weight target ÷ latest close) + a CSV document.
- Deduplicated in Mongo `notification_log` — safe to run the sweep many times a day.

## Setup (once)

1. Telegram: talk to **@BotFather** → `/newbot` → copy the bot token.
   Then message your new bot once, and get your chat id from
   `https://api.telegram.org/bot<TOKEN>/getUpdates` (the `chat.id` field).
2. In `market-hunt/.env`:
   ```
   TELEGRAM_BOT_TOKEN=<token>
   TELEGRAM_CHAT_ID=<chat id>
   ```
3. Test without sending: 
   ```
   .venv/Scripts/python.exe notify_rebalance.py --dry-run
   ```
   Then send for real (only fires inside the lead window): drop `--dry-run`.

## Schedule daily (Windows Task Scheduler)

Run at 08:30, after your data refresh (order matters — the preview is only as
fresh as the loaded prices/indicators):

```
schtasks /Create /TN "market-hunt-notify" /SC DAILY /ST 08:30 /TR "cmd /c cd /d D:\gs\workspace\tvagent\market-hunt && .venv\Scripts\python.exe notify_rebalance.py >> logs\notify.log 2>&1"
```

(Chain it after `scripts/refresh_all.py` if you schedule that too.)

## Manual trigger (API)

`POST /api/broker`-guarded (same rules: localhost, or X-API-Key when MH_API_TOKEN set):

```
curl -X POST "http://127.0.0.1:3001/api/notify/rebalance/run"                  # dry-run (default)
curl -X POST "http://127.0.0.1:3001/api/notify/rebalance/run?dry_run=false"    # send
```

## Honest limits

- Due dates are business-day estimates; an exchange holiday on the target day
  shifts the actual engine rebalance to the next session — the day-of message
  still fires on the estimated day, so sanity-check against the terminal.
- Quantities are estimates from the latest close; fills will differ. All orders
  are DELIVERY (CNC) — never intraday product types.
- If Telegram vars are unset, sends fail loud (non-dry runs report skipped).
