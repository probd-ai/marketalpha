"""Render a rebalance plan as a shareable PDF trade sheet.

The on-screen panel is for deciding; this is for *executing and sharing* — a dealer
or a second pair of eyes should be able to work the orders off the page without the
terminal open. Nothing is computed here: the plan arrives already built by
`portfolio_composition.build_rebalance_plan`, so the paper and the screen can never
disagree.

reportlab is already a project dependency (BSD-style licence, see requirements.txt).
"""
from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (HRFlowable, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

# Entries green, exits red, size changes amber — the same grammar as the screen.
ACTION_COLOR = {
    "buy": colors.HexColor("#047857"),
    "add": colors.HexColor("#059669"),
    "sell": colors.HexColor("#b91c1c"),
    "trim": colors.HexColor("#b45309"),
    "hold": colors.HexColor("#6b7280"),
    "unpriced": colors.HexColor("#92400e"),
}
GRID = colors.HexColor("#d1d5db")
HEAD_BG = colors.HexColor("#f3f4f6")
MUTED = colors.HexColor("#6b7280")

TRADE_ACTIONS = ("buy", "add", "trim", "sell", "unpriced")


def _inr(v: float) -> str:
    """Indian digit grouping — 12,34,567 not 1,234,567."""
    neg = v < 0
    s = f"{abs(v):,.0f}"
    if len(s.replace(",", "")) > 3:
        digits = s.replace(",", "")
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return ("-" if neg else "") + s


def _qty(v: float) -> str:
    return f"{v:+,.0f}" if v else "—"


def build_rebalance_pdf(plan: dict, *, portfolio_name: str, as_of: str,
                        due_sleeves: list[str] | None = None,
                        symbol_meta: dict | None = None,
                        generated_at: str | None = None) -> bytes:
    """Return PDF bytes for `plan` (the output of build_rebalance_plan)."""
    symbol_meta = symbol_meta or {}
    rows = plan.get("rows") or []
    summary = plan.get("summary") or {}
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=12 * mm, rightMargin=12 * mm, topMargin=10 * mm, bottomMargin=12 * mm,
        title=f"{portfolio_name} — rebalance plan {as_of}",
        author="market-hunt",
    )
    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=15, spaceAfter=2, alignment=0)
    sub = ParagraphStyle("sub", parent=ss["Normal"], fontSize=8.5, textColor=MUTED)
    note = ParagraphStyle("note", parent=ss["Normal"], fontSize=7.5, textColor=MUTED)
    right = ParagraphStyle("right", parent=ss["Normal"], fontSize=8, alignment=TA_RIGHT)

    story = [Paragraph(f"{portfolio_name} — rebalance plan", h1)]
    due = ", ".join(due_sleeves or []) or "no sleeve due"
    story.append(Paragraph(
        f"Prices as of <b>{as_of}</b> &nbsp;·&nbsp; due: <b>{due}</b> &nbsp;·&nbsp; "
        f"generated {generated_at or datetime.now().strftime('%Y-%m-%d %H:%M')}", sub))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", color=GRID, thickness=0.6))
    story.append(Spacer(1, 6))

    # ── summary strip ────────────────────────────────────────────────────────
    cells = [
        ("Buy", _inr(summary.get("buy_value", 0)), ACTION_COLOR["buy"]),
        ("Sell", _inr(summary.get("sell_value", 0)), ACTION_COLOR["sell"]),
        ("Net cash", _inr(summary.get("net_cash", 0)), colors.black),
        ("Positions",
         f"{summary.get('positions_before', 0)} → {summary.get('positions_after', 0)}"
         f"  (+{summary.get('new_positions', 0)}/-{summary.get('exited_positions', 0)})",
         colors.black),
        ("Turnover", f"{summary.get('turnover_percent', 0)}%", colors.black),
    ]
    strip = Table([[c[0] for c in cells], [c[1] for c in cells]],
                  colWidths=[doc.width / len(cells)] * len(cells))
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica", 7.5),
        ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
        ("FONT", (0, 1), (-1, 1), "Helvetica-Bold", 11),
        ("BACKGROUND", (0, 0), (-1, -1), HEAD_BG),
        ("BOX", (0, 0), (-1, -1), 0.6, GRID),
        ("TOPPADDING", (0, 0), (-1, 0), 5), ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]
    for i, c in enumerate(cells):
        style.append(("TEXTCOLOR", (i, 1), (i, 1), c[2]))
    strip.setStyle(TableStyle(style))
    story.extend([strip, Spacer(1, 10)])

    header = ["Action", "Symbol", "Tier", "Price", "Qty", "Value", "Now", "After", "✓"]
    widths = [16, 26, 24, 18, 16, 24, 24, 24, 10]
    scale = doc.width / sum(widths)
    col_widths = [w * scale for w in widths]

    def _table(section_rows: list[dict], title: str, subtitle: str):
        story.append(Paragraph(f"<b>{title}</b> &nbsp;<font size=7.5 color='#6b7280'>{subtitle}</font>",
                               ss["Normal"]))
        story.append(Spacer(1, 3))
        data = [header]
        for r in section_rows:
            tier = (symbol_meta.get(r["symbol"]) or {}).get("index_tier", "")
            data.append([
                r["action"].upper(), r["symbol"], tier,
                f"{r['price']:,.2f}", _qty(r["delta_qty"]),
                _inr(r["delta_value"]) if r["delta_value"] else "—",
                _inr(r["current_value"]), _inr(r["target_value"]), "",
            ])
        t = Table(data, colWidths=col_widths, repeatRows=1)
        ts = [
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
            ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
            ("FONT", (1, 1), (1, -1), "Helvetica-Bold", 8),
            ("ALIGN", (3, 0), (7, -1), "RIGHT"),
            ("ALIGN", (8, 0), (8, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.4, GRID),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]
        for i, r in enumerate(section_rows, start=1):
            ts.append(("TEXTCOLOR", (0, i), (0, i), ACTION_COLOR.get(r["action"], colors.black)))
            ts.append(("FONT", (0, i), (0, i), "Helvetica-Bold", 7.5))
            if r["delta_value"]:
                ts.append(("TEXTCOLOR", (5, i), (5, i),
                           ACTION_COLOR["buy"] if r["delta_value"] > 0 else ACTION_COLOR["sell"]))
            if i % 2 == 0:
                ts.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafafa")))
        t.setStyle(TableStyle(ts))
        story.append(t)
        story.append(Spacer(1, 10))

    trades = [r for r in rows if r["action"] in TRADE_ACTIONS]

    if trades:
        _table(trades, f"Orders to place ({len(trades)})",
               "tick the last column as each order fills")
    else:
        story.append(Paragraph("<b>No orders</b> — the book already matches its target.", ss["Normal"]))
        story.append(Spacer(1, 10))

    # ── the resulting book ───────────────────────────────────────────────────
    book = plan.get("book_after") or []
    if book:
        story.append(PageBreak())
        story.append(Paragraph(f"<b>Book after this rebalance</b> "
                               f"<font size=7.5 color='#6b7280'>{len(book)} positions · "
                               f"{_inr(sum(b['value'] for b in book))} deployed</font>", ss["Normal"]))
        story.append(Spacer(1, 4))

        mix_before = {m["name"]: m["weight"] for m in (plan.get("mix_before") or [])}
        mix_after = plan.get("mix_after") or []
        if mix_after:
            mix_rows = [["Tier", "Weight now", "Weight after", "Change", "Names"]]
            for m in mix_after:
                was = mix_before.get(m["name"], 0.0)
                delta = m["weight"] - was
                mix_rows.append([m["name"], f"{was:.1f}%", f"{m['weight']:.1f}%",
                                 f"{delta:+.1f}pp", str(m["count"])])
            mt = Table(mix_rows, colWidths=[doc.width * w for w in (.30, .16, .16, .16, .22)])
            mt.setStyle(TableStyle([
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
                ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.4, GRID),
                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.extend([mt, Spacer(1, 8)])

        bh = ["#", "Symbol", "Tier", "Sector", "Qty", "Price", "Value", "Weight", ""]
        bw = [8, 26, 24, 34, 16, 18, 24, 16, 8]
        bscale = doc.width / sum(bw)
        data = [bh]
        for i, b in enumerate(book, 1):
            data.append([str(i), b["symbol"], b["tier"], (b["sector"] or "")[:26],
                         f"{b['quantity']:,.0f}", f"{b['price']:,.2f}",
                         _inr(b["value"]), f"{b['weight']:.1f}%",
                         "NEW" if b["status"] == "new" else ""])
        bt = Table(data, colWidths=[w * bscale for w in bw], repeatRows=1)
        bts = [
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.5),
            ("BACKGROUND", (0, 0), (-1, 0), HEAD_BG),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
            ("FONT", (1, 1), (1, -1), "Helvetica-Bold", 8),
            ("ALIGN", (4, 0), (7, -1), "RIGHT"),
            ("ALIGN", (0, 0), (0, -1), "RIGHT"),
            ("ALIGN", (8, 0), (8, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.4, GRID),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]
        for i, b in enumerate(book, start=1):
            if b["status"] == "new":
                bts.append(("TEXTCOLOR", (8, i), (8, i), ACTION_COLOR["buy"]))
                bts.append(("FONT", (8, i), (8, i), "Helvetica-Bold", 6.5))
            if i % 2 == 0:
                bts.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fafafa")))
        bt.setStyle(TableStyle(bts))
        story.extend([bt, Spacer(1, 8)])

    unpriced = summary.get("unpriced") or []
    if unpriced:
        story.append(Paragraph(
            f"<b>Not sized:</b> {', '.join(unpriced)} — no price available, so no quantity was "
            f"invented. Size these manually before trading.", note))
        story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Hypothetical, point-in-time plan generated from stored prices — not advice, and not a "
        "record of executed trades. Quantities are whole shares; verify against live prices "
        "before placing orders.", note))

    doc.build(story)
    return buf.getvalue()
