'use client';

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import Link from 'next/link';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import {
  ArrowLeft, Home, RefreshCw, Activity, Zap, AlertTriangle,
} from 'lucide-react';
import { api } from '@/lib/api';

/** The four ratios, in the order they are drawn. The first three walk adjacent bands
 *  down the scale; the last is the wide strength-vs-weakness read. */
const RATIOS = [
  { key: '80-100/60-80', color: '#22c55e', label: '80-100 / 60-80' },
  { key: '60-80/40-60', color: '#3b82f6', label: '60-80 / 40-60' },
  { key: '40-60/20-40', color: '#f59e0b', label: '40-60 / 20-40' },
  { key: '80-100/20-40', color: '#a855f7', label: '80-100 / 20-40' },
];

const LOOKBACKS = ['1Y', '2Y', '3Y', '5Y', '10Y', 'MAX'];
const METRICS = [
  { value: 'truevx_score', label: 'TrueVX Score' },
  { value: 'mean_short', label: 'Mean Short (22d)' },
  { value: 'mean_mid', label: 'Mean Mid (66d)' },
  { value: 'mean_long', label: 'Mean Long (222d)' },
];

interface RatioRow {
  date: string;
  total: number;
  counts: Record<string, number>;
  ratios: Record<string, number | null>;
  price?: { close: number };
}

const fmt = (v: number | null | undefined, dp = 2) =>
  v === null || v === undefined || !isFinite(v) ? '—' : v.toFixed(dp);

const IndexRatiosPage = () => {
  const [indexSymbol, setIndexSymbol] = useState('NIFTY500');
  const [lookback, setLookback] = useState('5Y');
  const [metric, setMetric] = useState('truevx_score');
  const [pointInTime, setPointInTime] = useState(true);
  const [showPrice, setShowPrice] = useState(true);
  const [logScale, setLogScale] = useState(false);
  const [active, setActive] = useState<Record<string, boolean>>(
    Object.fromEntries(RATIOS.map((r) => [r.key, true]))
  );

  const [rows, setRows] = useState<RatioRow[]>([]);
  const [meta, setMeta] = useState<any>(null);
  const [availableIndices, setAvailableIndices] = useState<{ code: string; count: number }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const overview = await api.getDataOverview();
        const list = (overview.index_stats || [])
          .map((s: any) => ({ code: String(s._id).replace(/\s+/g, '').toUpperCase(), count: s.count }))
          .filter((s: any) => s.count > 10 && !s.code.includes('MARKETINDEXES'))
          .sort((a: any, b: any) => a.code.localeCompare(b.code));
        setAvailableIndices(list);
      } catch {
        setAvailableIndices([
          { code: 'NIFTY50', count: 50 }, { code: 'NIFTY200', count: 200 },
          { code: 'NIFTY500', count: 500 },
        ]);
      }
    })();
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getIndexRatios({
        indexSymbol, lookback, metric, pointInTime, includePrice: showPrice,
      });
      if (!res?.success) throw new Error(res?.error || 'Request failed');
      setRows(res.data || []);
      setMeta(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load ratios');
      setRows([]);
      setMeta(null);
    } finally {
      setLoading(false);
    }
  }, [indexSymbol, lookback, metric, pointInTime, showPrice]);

  useEffect(() => { load(); }, [load]);

  /** Flatten for recharts. A null ratio (empty denominator band) stays null so the line
   *  breaks rather than dropping to zero and implying a reading we do not have. */
  const chartData = useMemo(() => rows.map((r) => {
    const o: any = { date: r.date, price: r.price?.close ?? null, total: r.total };
    RATIOS.forEach((x) => { o[x.key] = r.ratios?.[x.key] ?? null; });
    return o;
  }), [rows]);

  const stats = useMemo(() => RATIOS.map((r) => {
    const vals = rows.map((x) => x.ratios?.[r.key]).filter((v): v is number => v !== null && v !== undefined);
    if (!vals.length) return { ...r, latest: null, min: null, max: null, median: null, gaps: rows.length };
    const sorted = [...vals].sort((a, b) => a - b);
    const last = rows[rows.length - 1]?.ratios?.[r.key];
    return {
      ...r,
      latest: last ?? null,
      min: sorted[0],
      max: sorted[sorted.length - 1],
      median: sorted[Math.floor(sorted.length / 2)],
      gaps: rows.length - vals.length,
    };
  }), [rows]);

  const latestRow = rows[rows.length - 1];
  const totalGaps = stats.reduce((a, s) => a + s.gaps, 0);

  const tickFmt = (d: string) => {
    const dt = new Date(d);
    return `${dt.toLocaleString('en-US', { month: 'short' })} '${String(dt.getFullYear()).slice(2)}`;
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <div className="max-w-[1600px] mx-auto p-6 space-y-6">

        {/* header */}
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-3">
            <Link href="/analytics">
              <Button variant="outline" size="sm"><ArrowLeft className="w-4 h-4 mr-1" />Analytics</Button>
            </Link>
            <Link href="/">
              <Button variant="outline" size="sm"><Home className="w-4 h-4" /></Button>
            </Link>
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">Index Band Ratios</h1>
              <p className="text-sm text-slate-400">
                How the score distribution is tilted between bands — comparable across indices
                and across rebalances, where raw counts are not.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {meta?.summary?.elapsed_ms !== undefined && (
              <Badge className="bg-slate-800 text-slate-300 border-slate-700">
                <Zap className="w-3 h-3 mr-1" />{meta.summary.elapsed_ms} ms
              </Badge>
            )}
            <Button onClick={load} disabled={loading} size="sm">
              <RefreshCw className={`w-4 h-4 mr-1 ${loading ? 'animate-spin' : ''}`} />
              {loading ? 'Loading' : 'Refresh'}
            </Button>
          </div>
        </div>

        {/* controls */}
        <Card className="bg-slate-900/60 border-slate-800">
          <CardContent className="p-4">
            <div className="flex flex-wrap items-end gap-4">
              <label className="flex flex-col gap-1">
                <span className="text-xs uppercase tracking-wide text-slate-400">Index</span>
                <select
                  value={indexSymbol}
                  onChange={(e) => setIndexSymbol(e.target.value)}
                  className="bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm min-w-[220px]"
                >
                  {!availableIndices.some((i) => i.code === indexSymbol) && (
                    <option value={indexSymbol}>{indexSymbol}</option>
                  )}
                  {availableIndices.map((i) => (
                    <option key={i.code} value={i.code}>{i.code} ({i.count})</option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col gap-1">
                <span className="text-xs uppercase tracking-wide text-slate-400">Lookback</span>
                <div className="flex gap-1">
                  {LOOKBACKS.map((lb) => (
                    <button
                      key={lb}
                      onClick={() => setLookback(lb)}
                      className={`px-2.5 py-1.5 text-sm rounded border ${
                        lookback === lb
                          ? 'bg-blue-600 border-blue-500 text-white'
                          : 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700'
                      }`}
                    >{lb}</button>
                  ))}
                </div>
              </label>

              <label className="flex flex-col gap-1">
                <span className="text-xs uppercase tracking-wide text-slate-400">Metric</span>
                <select
                  value={metric}
                  onChange={(e) => setMetric(e.target.value)}
                  className="bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm"
                >
                  {METRICS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
                </select>
              </label>

              <div className="flex items-center gap-4 pb-1.5 text-sm">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={pointInTime}
                         onChange={(e) => setPointInTime(e.target.checked)} className="accent-blue-500" />
                  <span>Point-in-time members</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={showPrice}
                         onChange={(e) => setShowPrice(e.target.checked)} className="accent-blue-500" />
                  <span>Benchmark overlay</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input type="checkbox" checked={logScale}
                         onChange={(e) => setLogScale(e.target.checked)} className="accent-blue-500" />
                  <span>Log scale</span>
                </label>
              </div>
            </div>
          </CardContent>
        </Card>

        {error && (
          <Card className="bg-red-950/40 border-red-900">
            <CardContent className="p-4 text-sm text-red-300 flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" />{error}
            </CardContent>
          </Card>
        )}

        {meta && pointInTime && !meta.point_in_time && (
          <Card className="bg-amber-950/30 border-amber-900">
            <CardContent className="p-3 text-sm text-amber-300 flex items-center gap-2">
              <AlertTriangle className="w-4 h-4" />
              No reconstitution history for <strong>{indexSymbol}</strong> — showing today&apos;s
              constituents across the whole window, which carries survivorship bias.
            </CardContent>
          </Card>
        )}

        {/* ratio summary tiles */}
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {stats.map((s) => (
            <Card key={s.key} className="bg-slate-900/60 border-slate-800">
              <CardContent className="p-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium tracking-wide" style={{ color: s.color }}>
                    {s.label}
                  </span>
                  <button
                    onClick={() => setActive((a) => ({ ...a, [s.key]: !a[s.key] }))}
                    className={`text-[10px] px-1.5 py-0.5 rounded border ${
                      active[s.key]
                        ? 'border-slate-600 text-slate-300'
                        : 'border-slate-800 text-slate-600'
                    }`}
                  >{active[s.key] ? 'shown' : 'hidden'}</button>
                </div>
                <div className="text-3xl font-semibold tabular-nums">{fmt(s.latest)}</div>
                <div className="mt-2 text-xs text-slate-400 tabular-nums flex gap-3">
                  <span>med {fmt(s.median)}</span>
                  <span>min {fmt(s.min)}</span>
                  <span>max {fmt(s.max)}</span>
                </div>
                {s.gaps > 0 && (
                  <div className="mt-1 text-[11px] text-amber-400/80">
                    {s.gaps} date{s.gaps === 1 ? '' : 's'} with an empty denominator
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>

        {/* chart */}
        <Card className="bg-slate-900/60 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <Activity className="w-4 h-4" />
              Band ratios over time
              {meta && (
                <span className="text-xs font-normal text-slate-400">
                  · {meta.summary?.date_count} sessions
                  {meta.point_in_time && ` · ${meta.summary?.epochs} membership epochs`}
                  {meta.summary?.symbols_latest ? ` · ${meta.summary.symbols_latest} members latest` : ''}
                </span>
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div style={{ width: '100%', height: 460 }}>
              <ResponsiveContainer>
                <LineChart data={chartData} margin={{ top: 8, right: 60, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="date" tickFormatter={tickFmt} stroke="#64748b"
                         tick={{ fontSize: 11 }} minTickGap={40} />
                  <YAxis yAxisId="ratio" stroke="#64748b" tick={{ fontSize: 11 }}
                         scale={logScale ? 'log' : 'auto'}
                         domain={logScale ? ['auto', 'auto'] : [0, 'auto']}
                         allowDataOverflow={logScale}
                         label={{ value: 'ratio', angle: -90, position: 'insideLeft',
                                  fill: '#64748b', fontSize: 11 }} />
                  {showPrice && (
                    <YAxis yAxisId="price" orientation="right" stroke="#475569"
                           tick={{ fontSize: 11 }} domain={['auto', 'auto']} />
                  )}
                  <Tooltip
                    contentStyle={{ background: '#0f172a', border: '1px solid #1e293b',
                                    borderRadius: 6, fontSize: 12 }}
                    labelStyle={{ color: '#94a3b8' }}
                    formatter={(v: any, name: string) =>
                      [v === null ? '— (empty denominator)' : Number(v).toFixed(3), name]}
                  />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <ReferenceLine yAxisId="ratio" y={1} stroke="#475569" strokeDasharray="4 4" />
                  {showPrice && (
                    <Line yAxisId="price" type="monotone" dataKey="price" name={meta?.base_symbol || 'benchmark'}
                          stroke="#475569" dot={false} strokeWidth={1} connectNulls />
                  )}
                  {RATIOS.filter((r) => active[r.key]).map((r) => (
                    <Line key={r.key} yAxisId="ratio" type="monotone" dataKey={r.key}
                          name={r.label} stroke={r.color} dot={false} strokeWidth={1.6}
                          connectNulls={false} />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
            <p className="mt-3 text-xs text-slate-500">
              The dashed line at 1.0 is parity — above it the upper band holds more names than the
              lower one. A line breaks where its denominator band was empty; that is missing
              information, not a ratio of zero.
              {totalGaps > 0 && ` ${totalGaps} such gap${totalGaps === 1 ? '' : 's'} in this window.`}
            </p>
          </CardContent>
        </Card>

        {/* latest band counts, so the ratios can be traced back to what produced them */}
        {latestRow && (
          <Card className="bg-slate-900/60 border-slate-800">
            <CardHeader className="pb-2">
              <CardTitle className="text-base">
                Band counts — {latestRow.date}
                <span className="ml-2 text-xs font-normal text-slate-400">
                  {latestRow.total} members · {METRICS.find((m) => m.value === metric)?.label}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-slate-400 border-b border-slate-800">
                      <th className="text-left font-medium py-2">Band</th>
                      {(meta?.bands || []).map((b: string) => (
                        <th key={b} className="text-right font-medium py-2 px-3">{b}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="border-b border-slate-800/60">
                      <td className="py-2 text-slate-400">Count</td>
                      {(meta?.bands || []).map((b: string) => (
                        <td key={b} className="text-right py-2 px-3 tabular-nums">
                          {latestRow.counts[b] ?? 0}
                        </td>
                      ))}
                    </tr>
                    <tr>
                      <td className="py-2 text-slate-400">Share</td>
                      {(meta?.bands || []).map((b: string) => (
                        <td key={b} className="text-right py-2 px-3 tabular-nums text-slate-400">
                          {latestRow.total
                            ? `${((latestRow.counts[b] ?? 0) / latestRow.total * 100).toFixed(1)}%`
                            : '—'}
                        </td>
                      ))}
                    </tr>
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
};

export default IndexRatiosPage;
