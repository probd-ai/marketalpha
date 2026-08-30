'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { ArrowLeft, Layers, Play, Pencil, Loader2, RefreshCw, Zap, Database, FileDown } from 'lucide-react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
  PieChart,
  Pie,
  Cell,
} from 'recharts';
import {
  Portfolio,
  RunResult,
  PreviewResult,
  RebalanceEvent,
  portfolioApi,
  inr,
  pct,
  EARLIEST_INCEPTION,
} from '@/lib/portfolioApi';

const POLL_MS = 3000;

const TIER_COLORS: Record<string, string> = {
  'Nifty 50': '#1d4ed8',
  'Nifty 100': '#3b82f6',
  'Midcap 150': '#8b5cf6',
  'Smallcap 250': '#f59e0b',
  'Microcap 250': '#ef4444',
  'Nifty 500 (other)': '#10b981',
  Other: '#9ca3af',
};
const tierColor = (name: string) => TIER_COLORS[name] ?? '#9ca3af';

// Entries green, exits red, size changes amber, untouched grey — so the eye finds
// the orders to place without reading the action column.
const PLAN_ACTION_STYLE: Record<string, string> = {
  buy: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  add: 'bg-emerald-50/60 text-emerald-600 border-emerald-100',
  sell: 'bg-red-50 text-red-700 border-red-200',
  trim: 'bg-amber-50 text-amber-700 border-amber-200',
  hold: 'bg-gray-50 text-gray-500 border-gray-200',
  unpriced: 'bg-amber-100 text-amber-800 border-amber-300',
};

function KpiTile({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'pos' | 'neg' }) {
  const color = tone === 'pos' ? 'text-emerald-600' : tone === 'neg' ? 'text-red-600' : 'text-gray-900';
  return (
    <div className="rounded-lg border border-gray-200 bg-white/60 px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-gray-500">{label}</div>
      <div className={`text-lg font-semibold leading-tight ${color}`}>{value}</div>
      {sub && <div className="text-[11px] text-gray-400">{sub}</div>}
    </div>
  );
}

function ReturnCell({ v }: { v: number | undefined | null }) {
  if (v === undefined || v === null) return <span className="text-gray-400">—</span>;
  return <span className={v >= 0 ? 'text-emerald-600' : 'text-red-600'}>{pct(v)}</span>;
}

export default function PortfolioDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [flags, setFlags] = useState<{ config_changed?: boolean; data_stale?: boolean }>({});
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [showAllPlanRows, setShowAllPlanRows] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Instant load: portfolio doc + newest cached run in parallel.
  useEffect(() => {
    portfolioApi
      .get(id)
      .then((d) => {
        setPortfolio(d.portfolio);
        setStartDate(d.portfolio.inception_date);
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load portfolio'));
    portfolioApi
      .lastRun(id)
      .then((d) => {
        if (d.result) {
          setResult(d.result);
          setFlags({ config_changed: d.config_changed, data_stale: d.data_stale });
        }
      })
      .catch(() => undefined); // no cache yet is fine
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [id]);

  const loadPreview = useCallback(() => {
    setPreviewLoading(true);
    portfolioApi
      .preview(id)
      .then(setPreview)
      .catch((e) => setError(e instanceof Error ? e.message : 'Preview failed'))
      .finally(() => setPreviewLoading(false));
  }, [id]);

  const run = useCallback(
    async (force: boolean) => {
      setError(null);
      setRunning(true);
      try {
        const res = await portfolioApi.run(id, {
          startDate: startDate || undefined,
          endDate: endDate || undefined,
          force,
        });
        const jobId = res.job_id;
        const poll = async () => {
          try {
            const st = await portfolioApi.runStatus(jobId);
            if (st.status === 'completed') {
              if (pollRef.current) clearInterval(pollRef.current);
              setResult(st.result);
              setFlags({});
              setRunning(false);
            } else if (st.status === 'failed') {
              if (pollRef.current) clearInterval(pollRef.current);
              setError(st.error || 'Run failed');
              setRunning(false);
            }
          } catch (e) {
            if (pollRef.current) clearInterval(pollRef.current);
            setError(e instanceof Error ? e.message : 'Polling failed');
            setRunning(false);
          }
        };
        await poll(); // cached runs complete on the first poll — no 3s wait
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = setInterval(poll, POLL_MS);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Run failed to start');
        setRunning(false);
      }
    },
    [id, startDate, endDate]
  );

  const s = result?.summary;
  const curve = useMemo(
    () =>
      (result?.curve || []).map((c) => ({
        date: c.date,
        Portfolio: c.portfolio_value,
        Benchmark: c.benchmark_value,
        Holdings: c.holdings_count ?? null,
      })),
    [result]
  );

  const tierAlloc = result?.allocation?.index_tier || [];
  const sectorAlloc = result?.allocation?.sector || [];
  const bookMeta = useMemo(() => {
    const m: Record<string, { index_tier?: string; sector?: string }> = {};
    for (const b of result?.current_book || []) m[b.symbol] = { index_tier: b.index_tier, sector: b.sector };
    return m;
  }, [result]);

  // Rebalance history slices: last 3 months + the latest rebalance date deep dive.
  const history = result?.rebalance_history || [];
  const { recentGroups, lastDateEvents } = useMemo(() => {
    if (history.length === 0) return { recentGroups: [] as [string, RebalanceEvent[]][], lastDateEvents: [] as RebalanceEvent[] };
    const asOf = history[history.length - 1].date;
    const cutoff = new Date(new Date(asOf).getTime() - 92 * 86400000).toISOString().slice(0, 10);
    const recent = history.filter((e) => e.date >= cutoff);
    const byDate = new Map<string, RebalanceEvent[]>();
    for (const e of recent) {
      const arr = byDate.get(e.date) || [];
      arr.push(e);
      byDate.set(e.date, arr);
    }
    const groups = Array.from(byDate.entries()).sort((a, b) => (a[0] < b[0] ? 1 : -1));
    const lastDate = history[history.length - 1].date;
    return { recentGroups: groups, lastDateEvents: history.filter((e) => e.date === lastDate) };
  }, [history]);

  const maxSectorW = Math.max(1, ...sectorAlloc.map((x) => x.weight));

  return (
    <div className="min-h-screen p-6 max-w-[1500px] mx-auto space-y-4">
      {/* ── Command bar ─────────────────────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <Link href="/analytics/portfolios" className="text-gray-500 hover:text-gray-800">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <Layers className="w-6 h-6 text-indigo-600" />
          <div>
            <h1 className="text-xl font-bold text-gray-900">{portfolio?.name ?? '…'}</h1>
            <div className="flex items-center gap-2 text-xs text-gray-500 flex-wrap">
              {portfolio && (
                <>
                  <span>{inr(portfolio.total_capital)}</span>
                  <span>· since {portfolio.inception_date}</span>
                  <Badge variant="secondary">HYPOTHETICAL{portfolio.point_in_time_membership ? ' · PIT' : ''}</Badge>
                  {result?.cached && (
                    <Badge variant="outline" className="text-[10px]">
                      <Database className="w-3 h-3 mr-1 inline" />
                      cached · as of {result.portfolio.as_of} · computed {result.computed_at?.slice(0, 16).replace('T', ' ')}
                    </Badge>
                  )}
                  {flags.config_changed && <Badge className="bg-amber-100 text-amber-800">config changed — rerun</Badge>}
                  {flags.data_stale && <Badge className="bg-amber-100 text-amber-800">new data loaded — rerun</Badge>}
                </>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <label className="text-xs text-gray-500">
            From{' '}
            <input
              type="date"
              min={EARLIEST_INCEPTION}
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="p-1.5 border border-gray-300 rounded-md text-sm"
            />
          </label>
          <label className="text-xs text-gray-500">
            To{' '}
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="p-1.5 border border-gray-300 rounded-md text-sm"
            />
          </label>
          <Button onClick={() => run(false)} disabled={running || !portfolio} title="Uses the cache when config and data are unchanged">
            {running ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Play className="w-4 h-4 mr-2" />}
            {running ? 'Running…' : 'Run'}
          </Button>
          <Button variant="outline" onClick={() => run(true)} disabled={running || !portfolio} title="Ignore cache, recompute">
            <RefreshCw className="w-4 h-4" />
          </Button>
          <Link href={`/analytics/portfolios/${id}/edit`}>
            <Button variant="outline">
              <Pencil className="w-4 h-4" />
            </Button>
          </Link>
        </div>
      </div>

      {error && <div className="rounded-md border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-700">{error}</div>}

      {!result && !running && (
        <Card>
          <CardContent className="py-16 text-center text-gray-500">
            No cached run yet — press <span className="font-medium">Run</span> to compute (first run takes a few minutes;
            after that it loads instantly from the database).
          </CardContent>
        </Card>
      )}
      {running && !result && (
        <Card>
          <CardContent className="py-16 text-center text-gray-500">
            <Loader2 className="w-8 h-8 animate-spin mx-auto mb-3" />
            Running point-in-time simulation — up to a few minutes…
          </CardContent>
        </Card>
      )}

      {result && s && (
        <>
          {/* ── KPI strip ─────────────────────────────────────────── */}
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-2">
            <KpiTile label="Value" value={inr(s.total_value)} />
            <KpiTile label="P&L" value={inr(s.total_pnl)} tone={s.total_pnl >= 0 ? 'pos' : 'neg'} />
            <KpiTile label="Return" value={pct(s.total_return)} tone={s.total_return >= 0 ? 'pos' : 'neg'} />
            <KpiTile label="Benchmark" value={pct(s.benchmark_return)} />
            <KpiTile label="Alpha" value={pct(s.alpha)} tone={s.alpha >= 0 ? 'pos' : 'neg'} />
            <KpiTile label="Sharpe" value={s.sharpe_ratio.toFixed(2)} />
            <KpiTile label="Max DD" value={pct(s.max_drawdown)} tone="neg" />
            <KpiTile label="Breadth" value={`${result.current_book.length}`} sub="names held" />
          </div>

          {/* ── Equity curve + index-tier allocation ──────────────── */}
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <Card className="xl:col-span-2">
              <CardHeader className="pb-1">
                <CardTitle className="text-base">
                  Equity curve <span className="text-xs font-normal text-gray-500">vs {portfolio?.benchmark_symbol}</span>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="h-72">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={curve}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={50} />
                      <YAxis tick={{ fontSize: 10 }} domain={['auto', 'auto']} tickFormatter={(v: number) => `${(v / 1000).toFixed(0)}k`} />
                      <Tooltip formatter={(v: number, name: string) => (name === 'Holdings' ? v : inr(v))} />
                      <Legend />
                      <Line type="monotone" dataKey="Portfolio" stroke="#4f46e5" dot={false} strokeWidth={2} />
                      <Line type="monotone" dataKey="Benchmark" stroke="#9ca3af" dot={false} strokeWidth={1.5} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-base">Index allocation <span className="text-xs font-normal text-gray-500">· contribution = pts of total return</span></CardTitle>
              </CardHeader>
              <CardContent>
                <div className="h-36">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie data={tierAlloc} dataKey="weight" nameKey="name" innerRadius={32} outerRadius={58} paddingAngle={2}>
                        {tierAlloc.map((a) => (
                          <Cell key={a.name} fill={tierColor(a.name)} />
                        ))}
                      </Pie>
                      <Tooltip formatter={(v: number) => `${v}%`} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <table className="w-full text-xs mt-2">
                  <thead>
                    <tr className="text-left text-[10px] uppercase text-gray-500 border-b">
                      <th className="py-1">Tier</th>
                      <th className="py-1 text-right">Names</th>
                      <th className="py-1 text-right">Now</th>
                      <th className="py-1 text-right">Avg (run)</th>
                      <th className="py-1 text-right">Contrib</th>
                      <th className="py-1 text-right" title="Chained return of held names, only while held — compounds selection timing, read with care">Held ret</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tierAlloc.map((a) => (
                      <tr key={a.name} className="border-b border-gray-100">
                        <td className="py-1">
                          <span className="inline-block w-2 h-2 rounded-full mr-1.5" style={{ background: tierColor(a.name) }} />
                          {a.name}
                        </td>
                        <td className="py-1 text-right">{a.count}</td>
                        <td className="py-1 text-right">{a.weight}%</td>
                        <td className="py-1 text-right">{a.avg_weight ?? '—'}%</td>
                        <td className="py-1 text-right font-medium">
                          <ReturnCell v={a.contribution_pts} />
                        </td>
                        <td className="py-1 text-right text-gray-400">
                          {a.run_return !== undefined ? `${a.run_return >= 0 ? '+' : ''}${a.run_return}%` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>
          </div>

          {/* ── Sector concentration + breadth trend ─────────────── */}
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-base">Sector concentration</CardTitle>
              </CardHeader>
              <CardContent className="space-y-1.5">
                {sectorAlloc.map((a) => (
                  <div key={a.name} className="text-xs">
                    <div className="flex justify-between mb-0.5">
                      <span className="text-gray-700">{a.name} <span className="text-gray-400">({a.count})</span></span>
                      <span>
                        <span className="font-medium">{a.weight}%</span>
                        <span className="text-gray-400"> · avg {a.avg_weight ?? '—'}%</span>{' '}
                        <ReturnCell v={a.contribution_pts} />
                      </span>
                    </div>
                    <div className="h-1.5 rounded bg-gray-100">
                      <div className="h-1.5 rounded bg-indigo-500" style={{ width: `${(a.weight / maxSectorW) * 100}%` }} />
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-base">Portfolio breadth <span className="text-xs font-normal text-gray-500">distinct names held over time</span></CardTitle>
              </CardHeader>
              <CardContent>
                <div className="h-56">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={curve}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                      <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={50} />
                      <YAxis tick={{ fontSize: 10 }} allowDecimals={false} domain={[0, 'auto']} />
                      <Tooltip />
                      <Line type="stepAfter" dataKey="Holdings" stroke="#0d9488" dot={false} strokeWidth={1.5} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>

          </div>

          {/* ── Next-rebalance preview ───────────────────────────────
              Full width on purpose: this is a working document you read
              row by row while placing orders, not a glanceable tile. */}
          <Card>
              <CardHeader className="pb-1 flex-row items-center justify-between space-y-0">
                <CardTitle className="text-base">
                  Next rebalance{' '}
                  <span className="text-xs font-normal text-gray-500">
                    {preview?.due_sleeves?.length
                      ? `${preview.due_sleeves.join(', ')} due — others unchanged`
                      : 'probable moves · plan ahead'}
                  </span>
                </CardTitle>
                <div className="flex items-center gap-2">
                  {preview?.plan && (
                    <a
                      href={portfolioApi.previewPdfUrl(id)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 px-2.5 py-1.5 text-xs hover:bg-gray-50"
                      title="Download the plan as a shareable trade sheet"
                    >
                      <FileDown className="w-4 h-4" /> PDF
                    </a>
                  )}
                  <Button size="sm" variant="outline" onClick={loadPreview} disabled={previewLoading}>
                    {previewLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                {!preview && !previewLoading && (
                  <p className="text-xs text-gray-500">
                    Press <Zap className="w-3 h-3 inline" /> to compute probable adds/exits from the latest data — decide a
                    day or two before the actual rebalance.
                  </p>
                )}

                {/* ── Order plan: what to actually key into the terminal ── */}
                {preview?.plan && (
                  <div className="rounded border border-gray-200">
                    <div className="grid grid-cols-5 gap-2 px-3 py-2.5 text-xs border-b border-gray-200 bg-gray-50">
                      <div>
                        <div className="text-gray-500">Buy</div>
                        <div className="text-lg font-semibold text-emerald-700">{inr(preview.plan.summary.buy_value)}</div>
                      </div>
                      <div>
                        <div className="text-gray-500">Sell</div>
                        <div className="text-lg font-semibold text-red-700">{inr(preview.plan.summary.sell_value)}</div>
                      </div>
                      <div>
                        <div className="text-gray-500">Net cash</div>
                        <div className="font-medium">{inr(preview.plan.summary.net_cash)}</div>
                      </div>
                      <div>
                        <div className="text-gray-500">Positions</div>
                        <div className="font-medium">
                          {preview.plan.summary.positions_before} → {preview.plan.summary.positions_after}
                          <span className="ml-1 text-gray-500">
                            (+{preview.plan.summary.new_positions}/−{preview.plan.summary.exited_positions})
                          </span>
                        </div>
                      </div>
                      <div>
                        <div className="text-gray-500">Turnover</div>
                        <div className="font-medium">{preview.plan.summary.turnover_percent}%</div>
                      </div>
                    </div>

                    <table className="w-full text-sm">
                      <thead className="text-gray-500">
                        <tr className="border-b border-gray-100">
                          <th className="text-left font-normal px-3 py-2">Action</th>
                          <th className="text-left font-normal px-3 py-2">Symbol</th>
                          <th className="text-right font-normal px-3 py-2">Price</th>
                          <th className="text-right font-normal px-3 py-2">Qty</th>
                          <th className="text-right font-normal px-3 py-2">Value</th>
                          <th className="text-right font-normal px-3 py-2">Now → After</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.plan.rows
                          .filter((r) => (showAllPlanRows ? true : r.action !== 'hold'))
                          .map((r) => (
                            <tr key={r.symbol} className="border-b border-gray-50 last:border-0">
                              <td className="px-3 py-1.5">
                                <Badge className={`text-[11px] border ${PLAN_ACTION_STYLE[r.action] ?? ''}`}>
                                  {r.action.toUpperCase()}
                                </Badge>
                              </td>
                              <td className="px-3 py-1.5 font-medium">
                                {r.symbol}
                                {preview.symbol_meta[r.symbol]?.index_tier && (
                                  <span className="ml-1 text-gray-400 font-normal">
                                    · {preview.symbol_meta[r.symbol].index_tier}
                                  </span>
                                )}
                              </td>
                              <td className="px-3 py-1.5 text-right tabular-nums">{r.price.toLocaleString('en-IN')}</td>
                              <td className="px-3 py-1.5 text-right tabular-nums">
                                {r.delta_qty > 0 ? '+' : ''}
                                {r.delta_qty.toLocaleString('en-IN')}
                              </td>
                              <td
                                className={`px-2 py-1 text-right tabular-nums ${
                                  r.delta_value > 0 ? 'text-emerald-700' : r.delta_value < 0 ? 'text-red-700' : 'text-gray-400'
                                }`}
                              >
                                {r.delta_value === 0 ? '—' : inr(r.delta_value)}
                              </td>
                              <td className="px-3 py-1.5 text-right tabular-nums text-gray-500">
                                {inr(r.current_value)} → {inr(r.target_value)}
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>

                    <div className="flex items-center justify-between px-3 py-1.5 border-t border-gray-100">
                      <button
                        className="text-xs text-blue-600 hover:underline"
                        onClick={() => setShowAllPlanRows((v) => !v)}
                      >
                        {showAllPlanRows ? 'Show only the orders' : 'Also show untouched holdings in this list'}
                      </button>
                      {preview.plan.summary.unpriced.length > 0 && (
                        <span className="text-xs text-amber-600">
                          no price, not sized: {preview.plan.summary.unpriced.join(', ')}
                        </span>
                      )}
                    </div>
                  </div>
                )}

                {/* ── The book you'll own once these orders fill ────────── */}
                {preview?.plan?.book_after && preview.plan.book_after.length > 0 && (
                  <div className="rounded border border-gray-200">
                    <div className="flex items-center justify-between px-3 py-2 border-b border-gray-200 bg-gray-50">
                      <span className="text-sm font-medium">
                        Book after this rebalance
                        <span className="ml-2 text-xs font-normal text-gray-500">
                          {preview.plan.book_after.length} positions ·{' '}
                          {inr(preview.plan.book_after.reduce((a, b) => a + b.value, 0))} deployed
                        </span>
                      </span>
                    </div>

                    {/* Tier mix now → after: the shape of the book in one line */}
                    <div className="px-3 py-2 border-b border-gray-100 space-y-1">
                      {preview.plan.mix_after.map((m) => {
                        const was = preview.plan!.mix_before.find((x) => x.name === m.name)?.weight ?? 0;
                        const delta = +(m.weight - was).toFixed(1);
                        return (
                          <div key={m.name} className="flex items-center gap-2 text-xs">
                            <span className="w-32 shrink-0 text-gray-600">{m.name}</span>
                            <div className="flex-1 h-2.5 rounded bg-gray-100 overflow-hidden">
                              <div
                                className="h-full rounded"
                                style={{ width: `${m.weight}%`, background: tierColor(m.name) }}
                              />
                            </div>
                            <span className="w-32 shrink-0 text-right tabular-nums text-gray-500">
                              {was.toFixed(1)}% → <span className="text-gray-900">{m.weight.toFixed(1)}%</span>
                              <span className={delta > 0 ? 'ml-1 text-emerald-600' : delta < 0 ? 'ml-1 text-red-600' : 'ml-1 text-gray-400'}>
                                {delta > 0 ? '+' : ''}{delta}pp
                              </span>
                            </span>
                            <span className="w-10 shrink-0 text-right text-gray-400">{m.count}</span>
                          </div>
                        );
                      })}
                    </div>

                    <table className="w-full text-sm">
                      <thead className="text-gray-500">
                        <tr className="border-b border-gray-100">
                          <th className="text-left font-normal px-3 py-2 w-8">#</th>
                          <th className="text-left font-normal px-3 py-2">Symbol</th>
                          <th className="text-left font-normal px-3 py-2">Tier</th>
                          <th className="text-left font-normal px-3 py-2">Sector</th>
                          <th className="text-right font-normal px-3 py-2">Qty</th>
                          <th className="text-right font-normal px-3 py-2">Price</th>
                          <th className="text-right font-normal px-3 py-2">Value</th>
                          <th className="text-right font-normal px-3 py-2">Weight</th>
                        </tr>
                      </thead>
                      <tbody>
                        {preview.plan.book_after.map((b, i) => (
                          <tr key={b.symbol} className="border-b border-gray-50 last:border-0">
                            <td className="px-3 py-1.5 text-gray-400 tabular-nums">{i + 1}</td>
                            <td className="px-3 py-1.5 font-medium">
                              {b.symbol}
                              {b.status === 'new' && (
                                <Badge className="ml-2 bg-emerald-50 text-emerald-700 border border-emerald-200 text-[10px]">
                                  NEW
                                </Badge>
                              )}
                            </td>
                            <td className="px-3 py-1.5">
                              <span className="inline-flex items-center gap-1.5 text-gray-600">
                                <span className="w-2 h-2 rounded-full" style={{ background: tierColor(b.tier) }} />
                                {b.tier}
                              </span>
                            </td>
                            <td className="px-3 py-1.5 text-gray-500 truncate max-w-[16rem]">{b.sector}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{b.quantity.toLocaleString('en-IN')}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{b.price.toLocaleString('en-IN')}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{inr(b.value)}</td>
                            <td className="px-3 py-1.5 text-right tabular-nums">{b.weight.toFixed(1)}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {preview?.sleeves.map((ps) => (
                  <div key={ps.id} className="rounded border border-gray-200 p-2">
                    <div className="flex justify-between text-xs mb-1">
                      <span className="font-medium">{ps.label}</span>
                      <span className="text-gray-500">
                        due ≈ {ps.next_due_estimate} · data {ps.data_as_of} · {ps.eligible_count} eligible
                      </span>
                    </div>
                    <div className="text-[11px] text-gray-500 mb-0.5">Probable entries</div>
                    <div className="flex flex-wrap gap-1 mb-1.5">
                      {ps.probable_adds.length === 0 && <span className="text-xs text-gray-400">none — book unchanged</span>}
                      {ps.probable_adds.map((a) => (
                        <Badge key={a.symbol} className="bg-emerald-50 text-emerald-700 border border-emerald-200 text-[11px]">
                          + {a.symbol} <span className="ml-1 opacity-60">{a.score}%</span>
                          {preview.symbol_meta[a.symbol]?.index_tier && (
                            <span className="ml-1 opacity-50">· {preview.symbol_meta[a.symbol].index_tier}</span>
                          )}
                        </Badge>
                      ))}
                    </div>
                    <div className="text-[11px] text-gray-500 mb-0.5">Probable exits</div>
                    <div className="flex flex-wrap gap-1">
                      {ps.probable_exits.length === 0 && <span className="text-xs text-gray-400">none</span>}
                      {ps.probable_exits.map((a) => (
                        <Badge key={a.symbol} className="bg-red-50 text-red-700 border border-red-200 text-[11px]">
                          − {a.symbol} <span className="ml-1 opacity-60">{a.score}%</span>
                        </Badge>
                      ))}
                    </div>
                  </div>
                ))}
                {preview && !preview.based_on_run && (
                  <p className="text-[11px] text-amber-600">No cached run found — diff base is empty (everything shows as entry).</p>
                )}
              </CardContent>
            </Card>

          {/* ── Rebalance history: last 3 months + deep dive ─────── */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-base">Rebalances — last 3 months</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 max-h-96 overflow-y-auto">
                {recentGroups.length === 0 && <p className="text-xs text-gray-500">No rebalances in window.</p>}
                {recentGroups.map(([date, events]) => (
                  <div key={date} className="rounded border border-gray-200 p-2">
                    <div className="text-xs font-medium text-gray-800 mb-1">{date}</div>
                    {events.map((e, i) => (
                      <div key={`${e.sleeve}-${i}`} className="text-xs mb-1.5">
                        <span className="text-gray-500">{e.sleeve}</span>{' '}
                        <span className="text-gray-400">· {e.holdings_count} names after</span>
                        <div className="flex flex-wrap gap-1 mt-1">
                          {e.added.map((sym) => (
                            <Badge key={`a-${sym}`} className="bg-emerald-50 text-emerald-700 border border-emerald-200 text-[10px]">
                              + {sym}
                            </Badge>
                          ))}
                          {e.exit_details.map((x) => (
                            <Badge key={`x-${x.symbol}`} className="bg-red-50 text-red-700 border border-red-200 text-[10px]">
                              − {x.symbol} <span className="ml-1 opacity-70">{pct(x.pnl_percent)}</span>
                            </Badge>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-base">
                  Last rebalance — deep detail{' '}
                  <span className="text-xs font-normal text-gray-500">{lastDateEvents[0]?.date}</span>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 max-h-96 overflow-y-auto">
                {lastDateEvents.map((e, i) => (
                  <div key={i}>
                    <div className="text-xs font-medium text-gray-700 mb-1">{e.sleeve}</div>
                    <table className="w-full text-xs mb-2">
                      <thead>
                        <tr className="text-left text-[10px] uppercase text-gray-500 border-b">
                          <th className="py-1">Action</th>
                          <th className="py-1">Symbol</th>
                          <th className="py-1">Tier</th>
                          <th className="py-1">Sector</th>
                          <th className="py-1 text-right">Exit P&L</th>
                        </tr>
                      </thead>
                      <tbody>
                        {e.added.map((sym) => (
                          <tr key={`ad-${sym}`} className="border-b border-gray-50">
                            <td className="py-1 text-emerald-600 font-medium">ADD</td>
                            <td className="py-1">{sym}</td>
                            <td className="py-1 text-gray-500">{bookMeta[sym]?.index_tier ?? '—'}</td>
                            <td className="py-1 text-gray-500">{bookMeta[sym]?.sector ?? '—'}</td>
                            <td className="py-1 text-right text-gray-400">—</td>
                          </tr>
                        ))}
                        {e.exit_details.map((x) => (
                          <tr key={`ex-${x.symbol}`} className="border-b border-gray-50">
                            <td className="py-1 text-red-600 font-medium">EXIT</td>
                            <td className="py-1">{x.symbol}</td>
                            <td className="py-1 text-gray-500">—</td>
                            <td className="py-1 text-gray-500">—</td>
                            <td className="py-1 text-right">
                              <ReturnCell v={x.pnl_percent} />
                            </td>
                          </tr>
                        ))}
                        {e.added.length === 0 && e.exit_details.length === 0 && (
                          <tr>
                            <td colSpan={5} className="py-2 text-gray-400">
                              No changes.
                            </td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          {/* ── Sleeve attribution + current book ────────────────── */}
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-base">Sleeve attribution</CardTitle>
              </CardHeader>
              <CardContent>
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-left text-[10px] uppercase text-gray-500 border-b">
                      <th className="py-1">Sleeve</th>
                      <th className="py-1 text-right">Wt</th>
                      <th className="py-1 text-right">Return</th>
                      <th className="py-1 text-right">Contrib</th>
                      <th className="py-1 text-right">Names</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.sleeves.map((a) => (
                      <tr key={a.id} className="border-b border-gray-100">
                        <td className="py-1.5 font-medium">{a.label}</td>
                        <td className="py-1.5 text-right">{a.weight}%</td>
                        <td className="py-1.5 text-right">
                          <ReturnCell v={a.total_return} />
                        </td>
                        <td className="py-1.5 text-right">{pct(a.contribution)}</td>
                        <td className="py-1.5 text-right">{a.holdings_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </CardContent>
            </Card>

            <Card className="xl:col-span-2">
              <CardHeader className="pb-1">
                <CardTitle className="text-base">
                  Current book <span className="text-xs font-normal text-gray-500">({result.current_book.length} names)</span>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto max-h-96 overflow-y-auto">
                  <table className="w-full text-xs">
                    <thead className="sticky top-0 bg-white/95">
                      <tr className="text-left text-[10px] uppercase text-gray-500 border-b">
                        <th className="py-1 pr-2">Symbol</th>
                        <th className="py-1 pr-2">Tier</th>
                        <th className="py-1 pr-2">Sector</th>
                        <th className="py-1 pr-2">Sleeve(s)</th>
                        <th className="py-1 pr-2 text-right">Price</th>
                        <th className="py-1 pr-2 text-right">Qty</th>
                        <th className="py-1 pr-2 text-right">Value</th>
                        <th className="py-1 pr-2 text-right">Wt</th>
                        <th className="py-1 text-right">P&L</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.current_book.map((b) => (
                        <tr key={b.symbol} className="border-b border-gray-50">
                          <td className="py-1 pr-2 font-medium">{b.symbol}</td>
                          <td className="py-1 pr-2">
                            <span className="inline-block w-2 h-2 rounded-full mr-1" style={{ background: tierColor(b.index_tier || 'Other') }} />
                            <span className="text-gray-500">{b.index_tier ?? '—'}</span>
                          </td>
                          <td className="py-1 pr-2 text-gray-500">{b.sector ?? '—'}</td>
                          <td className="py-1 pr-2 text-gray-400">{b.sleeve}</td>
                          <td className="py-1 pr-2 text-right">{b.current_price?.toFixed(2)}</td>
                          <td className="py-1 pr-2 text-right font-medium">
                            {b.quantity != null ? (Number.isInteger(b.quantity) ? b.quantity : b.quantity.toFixed(2)) : '—'}
                          </td>
                          <td className="py-1 pr-2 text-right">{inr(b.market_value)}</td>
                          <td className="py-1 pr-2 text-right">{b.weight}%</td>
                          <td className="py-1 text-right">
                            <ReturnCell v={b.pnl_percent} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
