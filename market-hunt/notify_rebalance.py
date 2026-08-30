"""Stage-1 rebalance notifications (Telegram) — READ-ONLY, no broker calls.

Two message kinds per portfolio sleeve, deduplicated in Mongo `notification_log`:
  preview  - LEAD_DAYS business days before the estimated due date: probable
             adds/exits from the latest data (plan ahead).
  orders   - on the due date: final action list with ESTIMATED quantities from
             the sleeve's equal-weight target and latest closes, plus a CSV.

Secrets: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env (never logged).
Run:  .venv/Scripts/python.exe notify_rebalance.py [--dry-run] [--lead-days 2]
Schedule daily ~08:30 IST after the data refresh (see docs/NOTIFICATIONS.md).
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

import requests

from brokers import config as broker_config

TELEGRAM_API = "https://api.telegram.org"
DEFAULT_LEAD_DAYS = 2


# ---------------------------------------------------------------- pure logic

def business_days_until(today: date, due: date) -> int:
    """Business days strictly between today and due (0 = due today)."""
    if due <= today:
        return 0
    n, d = 0, today
    while d < due:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def decide_kind(today: date, due: date, lead_days: int) -> str | None:
    """'orders' on due day, 'preview' within the lead window, else None."""
    bd = business_days_until(today, due)
    if due == today:
        return "orders"
    if 0 < bd <= lead_days:
        return "preview"
    return None


def _chip(items, sign):
    return " ".join(f"{sign}{x['symbol']}({x['score']:+.1f}%)" for x in items) or "none"


def format_preview_message(portfolio_name: str, sleeve: dict) -> str:
    return (
        f"<b>📅 Rebalance ahead — {portfolio_name} / {sleeve['label']}</b>\n"
        f"due ≈ <b>{sleeve['next_due_estimate']}</b> · data {sleeve['data_as_of']} · "
        f"{sleeve['eligible_count']} eligible\n"
        f"<b>Probable entries:</b> {_chip(sleeve['probable_adds'], '+')}\n"
        f"<b>Probable exits:</b> {_chip(sleeve['probable_exits'], '−')}\n"
        f"<i>Estimates from latest loaded data — refresh prices first for decision-grade.</i>"
    )


def build_order_lines(sleeve: dict, sleeve_value: float,
                      closes: dict[str, float]) -> list[dict]:
    """SELL exits fully; BUY adds at equal-weight target. Quantities are
    ESTIMATES from the latest close — actual fills differ."""
    n_target = max(len(sleeve.get("holds", [])) + len(sleeve.get("probable_adds", [])), 1)
    per_name = sleeve_value / n_target
    lines = []
    for x in sleeve.get("probable_exits", []):
        lines.append({"action": "SELL", "symbol": x["symbol"], "qty": "ALL",
                      "est_value": None, "score": x["score"]})
    for x in sleeve.get("probable_adds", []):
        px = closes.get(x["symbol"])
        qty = int(per_name // px) if px else None
        lines.append({"action": "BUY", "symbol": x["symbol"], "qty": qty,
                      "est_value": round(qty * px, 0) if qty and px else round(per_name, 0),
                      "score": x["score"]})
    return lines


def format_orders_message(portfolio_name: str, sleeve: dict, lines: list[dict]) -> str:
    if not lines:
        body = "book unchanged — no orders."
    else:
        rows = []
        for ln in lines:
            qty = ln["qty"] if ln["qty"] is not None else "?"
            val = f" ≈₹{ln['est_value']:,.0f}" if ln.get("est_value") else ""
            rows.append(f"{'🟢' if ln['action'] == 'BUY' else '🔴'} "
                        f"<b>{ln['action']}</b> {ln['symbol']} × {qty}{val}")
        body = "\n".join(rows)
    return (
        f"<b>🔔 Rebalance TODAY — {portfolio_name} / {sleeve['label']}</b>\n"
        f"{body}\n"
        f"<i>Quantities are estimates (latest close, equal-weight target). "
        f"Place as DELIVERY orders. Verify against the terminal before punching.</i>"
    )


def orders_csv(portfolio_name: str, per_sleeve: list[tuple[dict, list[dict]]]) -> str:
    rows = ["portfolio,sleeve,action,symbol,qty,est_value,score"]
    for sleeve, lines in per_sleeve:
        for ln in lines:
            rows.append(f"{portfolio_name},{sleeve['label']},{ln['action']},"
                        f"{ln['symbol']},{ln['qty']},{ln['est_value'] or ''},{ln['score']}")
    return "\n".join(rows)


# ------------------------------------------------------------- side effects

def send_telegram(text: str, dry_run: bool = False) -> bool:
    token = broker_config.get("TELEGRAM_BOT_TOKEN")
    chat = broker_config.get("TELEGRAM_CHAT_ID")
    if dry_run or not (token and chat):
        print(("[dry-run] " if dry_run else "[telegram unconfigured] ") + text.replace("\n", " | ")[:400])
        return dry_run  # unconfigured is a failure unless dry-run
    r = requests.post(f"{TELEGRAM_API}/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True},
                      timeout=15)
    return r.status_code == 200


def send_telegram_document(filename: str, content: str, caption: str,
                           dry_run: bool = False) -> bool:
    token = broker_config.get("TELEGRAM_BOT_TOKEN")
    chat = broker_config.get("TELEGRAM_CHAT_ID")
    if dry_run or not (token and chat):
        print(f"[dry-run doc] {filename}: {len(content)} bytes")
        return dry_run
    r = requests.post(f"{TELEGRAM_API}/bot{token}/sendDocument",
                      data={"chat_id": chat, "caption": caption},
                      files={"document": (filename, content.encode())},
                      timeout=30)
    return r.status_code == 200


def _latest_closes(db, symbols: list[str]) -> dict[str, float]:
    closes: dict[str, float] = {}
    if not symbols:
        return closes
    for cname in sorted((c for c in db.list_collection_names()
                         if c.startswith("prices_")), reverse=True):
        missing = [s for s in symbols if s not in closes]
        if not missing:
            break
        for doc in db[cname].aggregate([
                {"$match": {"symbol": {"$in": missing}}},
                {"$sort": {"date": -1}},
                {"$group": {"_id": "$symbol", "close": {"$first": "$close_price"}}}]):
            closes[doc["_id"]] = float(doc["close"])
    return closes


def run(lead_days: int = DEFAULT_LEAD_DAYS, dry_run: bool = False,
        today: date | None = None) -> dict:
    """Check every portfolio; send due notifications; dedup via notification_log."""
    import api_server
    from app.db import mongo_conn
    from app.routers.portfolio import _compute_preview_sync
    if mongo_conn.db is None:
        mongo_conn.connect()
    db = mongo_conn.db
    today = today or datetime.now().date()
    sent, skipped = [], []

    for p in db.portfolios.find():
        try:
            preview = _compute_preview_sync(p)
        except Exception as e:
            skipped.append({"portfolio": p["name"], "reason": f"preview failed: {e}"})
            continue

        orders_bundle: list[tuple[dict, list[dict]]] = []
        last_run = db.portfolio_runs.find_one({"portfolio_id": p["id"]},
                                              {"result.summary": 1, "result.sleeves": 1},
                                              sort=[("created_at", -1)])
        total_value = ((last_run or {}).get("result") or {}).get("summary", {}) \
            .get("total_value", p.get("total_capital", 0))

        for sleeve in preview["sleeves"]:
            due = datetime.strptime(sleeve["next_due_estimate"], "%Y-%m-%d").date()
            kind = decide_kind(today, due, lead_days)
            if not kind:
                continue
            dedup = {"portfolio_id": p["id"], "sleeve_id": sleeve["id"],
                     "due_date": due.isoformat(), "kind": kind}
            if db.notification_log.find_one(dedup):
                skipped.append({**dedup, "reason": "already sent"})
                continue

            if kind == "preview":
                ok = send_telegram(format_preview_message(p["name"], sleeve), dry_run)
            else:
                weight = next((s["weight"] for s in p["sleeves"] if s["id"] == sleeve["id"]), 0)
                sleeve_value = total_value * float(weight) / 100.0
                syms = [x["symbol"] for x in sleeve.get("probable_adds", [])]
                lines = build_order_lines(sleeve, sleeve_value, _latest_closes(db, syms))
                ok = send_telegram(format_orders_message(p["name"], sleeve, lines), dry_run)
                if ok and lines:
                    orders_bundle.append((sleeve, lines))

            if ok and not dry_run:
                db.notification_log.insert_one({**dedup, "sent_at": datetime.now().isoformat()})
            (sent if ok else skipped).append({**dedup, "dry_run": dry_run})

        if orders_bundle:
            send_telegram_document(
                f"orders_{p['name'].replace(' ', '_')}_{today.isoformat()}.csv",
                orders_csv(p["name"], orders_bundle),
                f"{p['name']} — rebalance order list {today.isoformat()}", dry_run)

    return {"sent": sent, "skipped": skipped, "date": today.isoformat(), "dry_run": dry_run}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print instead of sending")
    ap.add_argument("--lead-days", type=int, default=DEFAULT_LEAD_DAYS)
    args = ap.parse_args()
    broker_config.load_env()
    out = run(lead_days=args.lead_days, dry_run=args.dry_run)
    print(f"\nsent: {len(out['sent'])}  skipped: {len(out['skipped'])}")
    for s in out["skipped"]:
        print("  skip:", s)
