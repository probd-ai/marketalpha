'use client';

// ONE table for the book: what the system wants, what the broker actually holds, and
// the difference — rather than two cards the reader has to join by eye.
//
// Row set is the UNION of the system book and the live holdings, so a position that
// exists on only one side is still visible. Ordering is by what needs doing: gaps
// first (largest first), then matched, then the user's own holdings.
//
// Quantities only. Broker prices are from whenever the export was downloaded and ours
// from the last data refresh, so any value comparison would differ for reasons that
// carry no information. Value/weight/P&L columns come from the system book alone and
// are shown as context, never compared.

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { AlertTriangle, CheckCircle2, RefreshCw } from 'lucide-react';
import { HoldingsStatus, SYNC_UI, TONE_CLASS, holdingsApi } from '@/lib/holdingsApi';
import { BookRow, inr } from '@/lib/portfolioApi';

// States where live numbers cannot be trusted. The live columns are then withheld
// rather than shown with a caveat — a stale comparison must never look reassuring.
const BLOCKED = new Set(['NO_PATH', 'NO_FILE', 'WRONG_ACCOUNT', 'UNRESOLVED',
  'REBALANCE_DUE', 'RUN_STALE', 'AMBIGUOUS']);

type Kind = 'BUY' | 'SELL' | 'OK' | 'MINE';
type SortKey = 'action' | 'symbol' | 'sector' | 'target' | 'live' | 'm1' | 'm3' | 'y1' | 'str' | 'value';

interface Row {
  symbol: string;
  book?: BookRow;
  target: number | null;
  live: number | null;
  kind: Kind;
  delta: number;
}

const KIND_STYLE: Record<Kind, string> = {
  BUY: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  SELL: 'bg-red-50 text-red-700 border-red-200',
  OK: 'bg-gray-50 text-gray-400 border-gray-200',
  MINE: 'bg-indigo-50 text-indigo-600 border-indigo-200',
};

function SortTh({ k, cur, desc, set, right, title, children }: {
  k: SortKey; cur: SortKey; desc: boolean; set: (k: SortKey) => void;
  right?: boolean; title?: string; children: React.ReactNode;
}) {
  const active = cur === k;
  return (
    <th title={title}
        onClick={() => set(k)}
        className={`py-1 pr-2 cursor-pointer select-none hover:text-gray-800 ${right ? 'text-right' : ''} ${active ? 'text-gray-800' : ''}`}>
      {children}{active ? (desc ? ' ↓' : ' ↑') : ''}
    </th>
  );
}

function pct(v: number | null | undefined) {
  if (v == null) return <span className="text-gray-300">—</span>;
  return <span className={v >= 0 ? 'text-emerald-600' : 'text-red-600'}>{v.toFixed(0)}%</span>;
}

/** Trailing returns. `strength` is highlighted past +150 because that is where a
 *  position is extended enough to be worth a second look before adding to it. */
function RocCells({ roc }: { roc?: { m1: number | null; m3: number | null; y1: number | null; strength: number | null } }) {
  const st = roc?.strength ?? null;
  const hot = st != null && st >= 150;
  return (
    <>
      <td className="py-1 pr-2 text-right">{pct(roc?.m1)}</td>
      <td className="py-1 pr-2 text-right">{pct(roc?.m3)}</td>
      <td className="py-1 pr-2 text-right">{pct(roc?.y1)}</td>
      <td className={`py-1 pr-2 text-right font-medium ${hot ? 'text-amber-600' : ''}`}
          title={hot ? 'Already run a long way — check position size before adding' : undefined}>
        {st == null ? <span className="text-gray-300">—</span> : `${st.toFixed(0)}%`}
      </td>
    </>
  );
}

export default function BookReconciliationCard({
  portfolioId, book, tierColor,
}: {
  portfolioId: string;
  book: BookRow[];
  tierColor: (t: string) => string;
}) {
  const [s, setS] = useState<HoldingsStatus | null>(null);
  const [busy, setBusy] = useState(false);
  // Default is action order — what needs trading, biggest first. Any column can be
  // sorted; `str` is offered because an extended name deserves a second look before
  // it is added to, not because high strength means buy first.
  const [sortKey, setSortKey] = useState<SortKey>('action');
  const [desc, setDesc] = useState(true);

  const load = useCallback(async () => {
    setBusy(true);
    try { setS(await holdingsApi.status(portfolioId)); } catch { /* page must survive */ }
    finally { setBusy(false); }
  }, [portfolioId]);

  useEffect(() => { load(); }, [load]);

  const byId: Record<string, BookRow> = {};
  book.forEach((b) => { byId[b.symbol] = b; });

  const blocked = !s || BLOCKED.has(s.sync.state);
  const target = s?.target || {};
  const live = s?.observed || {};
  const disc = s?.discretionary || {};

  let rows: Row[];
  if (blocked) {
    rows = book.map((b) => ({ symbol: b.symbol, book: b, target: b.quantity,
                              live: null, kind: 'OK' as Kind, delta: 0 }));
  } else {
    const symbols = Array.from(new Set([
      ...Object.keys(target), ...Object.keys(live), ...Object.keys(disc), ...book.map((b) => b.symbol),
    ]));
    rows = symbols.map((sym) => {
      const t = target[sym] ?? null;
      const rawLive = live[sym] ?? null;
      const mine = disc[sym] ?? 0;
      const netLive = rawLive === null ? null : Math.max(0, rawLive - mine);
      if (t === null || t === 0) {
        return { symbol: sym, book: byId[sym], target: t, live: rawLive,
                 kind: mine ? 'MINE' : ('SELL' as Kind), delta: netLive ?? 0 };
      }
      const d = t - (netLive ?? 0);
      return { symbol: sym, book: byId[sym], target: t, live: rawLive,
               kind: d === 0 ? 'OK' : d > 0 ? 'BUY' : 'SELL', delta: Math.abs(d) };
    });
    // Only positions the strategy never wanted AND that are unmarked are actionable
    // sells; a marked one is the user's own and must never be queued for sale.
    rows = rows.map((r) => (r.target ? r : { ...r, kind: disc[r.symbol] ? 'MINE' : r.kind }));
  }

  const order: Record<Kind, number> = { BUY: 0, SELL: 1, OK: 2, MINE: 3 };
  const pick = (r: Row): number | string => {
    const roc = s?.roc?.[r.symbol];
    switch (sortKey) {
      case 'symbol': return r.symbol;
      case 'sector': return r.book?.sector ?? '';
      case 'target': return r.target ?? -1;
      case 'live': return r.live ?? -1;
      case 'm1': return roc?.m1 ?? -1e9;
      case 'm3': return roc?.m3 ?? -1e9;
      case 'y1': return roc?.y1 ?? -1e9;
      case 'str': return roc?.strength ?? -1e9;
      case 'value': return r.book?.market_value ?? -1;
      default: return 0;
    }
  };
  if (sortKey === 'action') {
    rows.sort((a, b) => order[a.kind] - order[b.kind] || b.delta - a.delta
      || a.symbol.localeCompare(b.symbol));
  } else {
    rows.sort((a, b) => {
      const x = pick(a); const y = pick(b);
      const c = typeof x === 'string' ? x.localeCompare(y as string) : (x as number) - (y as number);
      return desc ? -c : c;
    });
  }

  const setSort = (k: SortKey) => {
    if (k === sortKey) setDesc(!desc); else { setSortKey(k); setDesc(true); }
  };

  const ui = s ? (SYNC_UI[s.sync.state] ?? { label: s.sync.state, tone: 'warn' as const }) : null;
  const actionable = rows.filter((r) => r.kind === 'BUY' || r.kind === 'SELL').length;
  const matched = rows.filter((r) => r.kind === 'OK' && r.live !== null).length;

  return (
    <Card>
      <CardHeader className="pb-1">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <CardTitle className="text-base">
            Book · system vs live{' '}
            <span className="text-xs font-normal text-gray-500">
              ({rows.length} names{!blocked && ` · ${matched} matched · ${actionable} to trade`})
            </span>
          </CardTitle>
          <div className="flex items-center gap-2">
            {ui && <span className={`text-xs px-2 py-0.5 rounded border ${TONE_CLASS[ui.tone]}`}>{ui.label}</span>}
            <Button variant="outline" size="sm" onClick={load} disabled={busy}>
              <RefreshCw className={`w-4 h-4 ${busy ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-2">
        {s && (
          <div className={`rounded border px-3 py-2 text-xs flex items-start gap-2 ${TONE_CLASS[ui!.tone]}`}>
            {ui!.tone === 'good' ? <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
                                 : <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />}
            <span>
              {s.sync.message}
              {s.sync.state === 'NO_PATH' && (
                <> <Link href={`/analytics/portfolios/${portfolioId}/edit`} className="underline">Map a folder →</Link></>
              )}
            </span>
          </div>
        )}

        {!!s?.sources?.length && (
          <div className="text-[11px] text-gray-500">
            {s.sources.map((src) => (
              <span key={src.file} className="mr-3">
                <span className="font-mono">{src.file}</span> · {src.broker} · {src.positions} pos
                {src.account ? ` · acct ${src.account.slice(0, 6)}` : ''}
              </span>
            ))}
            <span>
              dated {(s.file_as_of || '').replace('T', ' ').slice(0, 16)}
              {s.as_of_source === 'file_mtime' && ' (file date — broker states none)'}
              {(s.sources.length || 0) > 1 && ' · oldest across accounts'}
            </span>
          </div>
        )}

        <div className="overflow-x-auto max-h-[36rem] overflow-y-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-white/95">
              <tr className="text-left text-[10px] uppercase text-gray-500 border-b">
                <SortTh k="symbol" cur={sortKey} desc={desc} set={setSort}>Symbol</SortTh>
                <th className="py-1 pr-2">Tier</th>
                <SortTh k="sector" cur={sortKey} desc={desc} set={setSort}>Sector</SortTh>
                <SortTh k="target" cur={sortKey} desc={desc} set={setSort} right>System</SortTh>
                <SortTh k="live" cur={sortKey} desc={desc} set={setSort} right>Live</SortTh>
                <SortTh k="action" cur={sortKey} desc={desc} set={setSort}>Action</SortTh>
                <SortTh k="m1" cur={sortKey} desc={desc} set={setSort} right>1M</SortTh>
                <SortTh k="m3" cur={sortKey} desc={desc} set={setSort} right>3M</SortTh>
                <SortTh k="y1" cur={sortKey} desc={desc} set={setSort} right>1Y</SortTh>
                <SortTh k="str" cur={sortKey} desc={desc} set={setSort} right
                        title="Weighted mean of 1M/3M/1Y (0.5/1.0/1.5) — how far the name has already run. NOT a forecast: within already-strong names, the most-rising quintile has historically returned LESS.">Str</SortTh>
                <th className="py-1 pr-2 text-right">Price</th>
                <SortTh k="value" cur={sortKey} desc={desc} set={setSort} right>Value</SortTh>
                <th className="py-1 pr-2 text-right">Wt</th>
                <th className="py-1 text-right">P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol} className="border-b border-gray-50">
                  <td className="py-1 pr-2 font-medium">{r.symbol}</td>
                  <td className="py-1 pr-2">
                    <span className="inline-block w-2 h-2 rounded-full mr-1"
                          style={{ background: tierColor(r.book?.index_tier || 'Other') }} />
                    <span className="text-gray-500">{r.book?.index_tier ?? '—'}</span>
                  </td>
                  <td className="py-1 pr-2 text-gray-500">{r.book?.sector ?? '—'}</td>
                  <td className="py-1 pr-2 text-right font-medium">
                    {r.target === null ? '—'
                      : Number.isInteger(r.target) ? r.target : r.target.toFixed(2)}
                  </td>
                  <td className="py-1 pr-2 text-right">
                    {blocked ? <span className="text-gray-300">—</span>
                      : r.live === null ? <span className="text-gray-300">0</span> : r.live}
                  </td>
                  <td className="py-1 pr-2">
                    {blocked ? null : (
                      <span className={`px-1.5 py-0.5 rounded border ${KIND_STYLE[r.kind]}`}>
                        {r.kind === 'OK' ? 'match' : r.kind === 'MINE' ? 'yours' : `${r.kind} ${r.delta}`}
                      </span>
                    )}
                  </td>
                  <RocCells roc={s?.roc?.[r.symbol]} />
                  <td className="py-1 pr-2 text-right">{r.book?.current_price?.toFixed(2) ?? '—'}</td>
                  <td className="py-1 pr-2 text-right">{r.book ? inr(r.book.market_value) : '—'}</td>
                  <td className="py-1 pr-2 text-right">{r.book ? `${r.book.weight}%` : '—'}</td>
                  <td className="py-1 text-right">
                    {r.book?.pnl_percent != null
                      ? <span className={r.book.pnl_percent >= 0 ? 'text-emerald-600' : 'text-red-600'}>
                          {r.book.pnl_percent.toFixed(1)}%
                        </span>
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="text-[11px] text-gray-400">
          Quantities only — broker prices come from the export&apos;s own moment and are never
          reconciled. Value, weight and P&amp;L are the system book&apos;s, shown as context.
          {' '}<Link href={`/analytics/portfolios/${portfolioId}/edit`} className="underline">
            Mark holdings as yours →
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}
