# Fund desk — AI Broadcast (papers published by the AI)

Status: APPROVED 2026-10-04 (user request) · extends `2026-09-27-fund-trading-desk-design.md`.

## 1. Goal

A place in the desk where the AI publishes **papers** — trade plans, rebalance briefs, research notes — that the
manager reads, prints and archives. First paper: the plan to bring the equity book in sync with model version 4.

## 2. Paper format (structured, rendered by the desk)

```
{ title, kind: "trade-plan" | "research" | "note", summary, run_id?, sections: [
    { heading, body?, note?: {tone: "info" | "warn", text}, table?: {columns: [str], rows: [[str|number]],
      align?: ["left"|"right"], total?: [str|number] } } ] }
```

- Text only — no HTML or Markdown is interpreted; React escapes everything, so a paper cannot run code.
- Limits: title ≤ 120 chars, summary ≤ 1 000, 1–30 sections, heading ≤ 120, body ≤ 8 000, note ≤ 1 000,
  table ≤ 12 columns × 300 rows, cell ≤ 200 chars; every row the width of `columns`.
- `run_id`, when given, must be an existing run. Author is always "Claude" (set by the server).

## 3. Storage and API

- Collection `fund_papers`: `{_id: P<yyyymmdd>-<hex6>, ..., author, status: PUBLISHED | ARCHIVED, created_at}`.
- `GET /api/fund/papers` (newest first, no sections) · `GET /api/fund/papers/{id}` ·
  `POST /api/fund/papers` (publish) · `POST /api/fund/papers/{id}/archive`.
- Same access rules as the rest of `/api/fund`: localhost-only unless `MH_API_TOKEN`, desk header on writes.
- Journal `PAPER_PUBLISHED` / `PAPER_ARCHIVED`.

## 4. Desk

Sidebar **AI Broadcast** → list (title, kind, date, summary, linked run, archived toggle) → paper page (sections as
paragraphs, notes and tables; Print; Archive).

## 5. Tests

`tests/fund_desk/test_papers.py`: validation limits, publish → list → get → archive lifecycle, unknown run refused.
Frontend: paper renderer (vitest), `tsc`, browser check.

⚠ Security-sensitive (stored content on a write endpoint) — flag for human review before merge.
