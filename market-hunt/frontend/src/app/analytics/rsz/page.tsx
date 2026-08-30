'use client';

/**
 * RSZ — strong stocks, split by how they move.
 *
 * This page deliberately does NOT rank. RSZ was tested as a ranker across 45
 * backtests and never beat a plain price return, so ranking was taken out of the
 * strategy and simulator surfaces entirely. What its maths does measure well is
 * VOLATILITY among names that are already strong — so the page answers one question:
 * of the stocks whose 22/66/222-day relative strength all agree, which are the smooth
 * compounders to hold, and which are the jumpy ones worth trading the swings of?
 *
 * Served precomputed: GET /api/rsz/coverage | /swing | /symbol/{s} | /validation
 * Recompute with scripts/compute_rsz.py.
 */
import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import {
  ResponsiveContainer, LineChart, Line, ScatterChart, Scatter, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, ReferenceLine, Cell, ZAxis,
} from 'recharts';
import { ArrowLeft, Waves, TrendingUp, Activity, Info, Search } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';

const C = {
  smooth: '#0f766e',
  volatile: '#c2410c',
  grid: '#e5e7eb',
  pos: '#059669',
  neg: '#dc2626',
};

const SEGMENT_LABEL: Record<string, string> = {
  NIFTY50: 'Nifty 50',
  NIFTY100: 'Nifty 100 (ex-50)',
  NIFTYMIDCAP150: 'Midcap 150',
  NIFTYSMALLCAP250: 'Smallcap 250',
  NIFTYMICROCAP250: 'Microcap 250',
  NIFTY500: 'Nifty 500 (other)',
};

interface Row {
  symbol: string; segment: string; rsz_score: number; aligned: number;
  vol_pct: number; z_comp: number; r_short: number; r_mid: number; r_long: number;
  price: number | null;
  ret_1m: number | null; ret_3m: number | null; ret_12m: number | null;
  avg_daily_range_pct?: number | null; dips_per_year?: number | null;
  median_dip_pct?: number | null; max_dip_pct?: number | null;
  recovery_rate_pct?: number | null; dip_episodes?: number;
}
interface Swing {
  date: string; benchmark: string; qualifying: number; shown: number;
  cut_vol_pct: number | null; dip_threshold_pct: number; recovery_window: number;
  smooth: Row[]; volatile: Row[]; message?: string;
}
interface Coverage {
  ready: boolean; message?: string; benchmark: string; rows: number; symbols: number;
  first_date: string; latest_date: string;
  segments: { segment: string; names: number }[];
  params: { horizons: number[]; weights: number[]; aligned_rank_floor: number; vol_window: number };
}

const pct = (v: number | null | undefined, d = 1) =>
  v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(d)}%`;
const plain = (v: number | null | undefined, d = 1, suf = '%') =>
  v === null || v === undefined ? '—' : `${v.toFixed(d)}${suf}`;

function Ret({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="text-gray-400">—</span>;
  return <span className={v >= 0 ? 'text-emerald-600' : 'text-red-600'}>{pct(v)}</span>;
}

/** Recovery rate is the number that decides whether a dip is buyable or a trap. */
function Recovery({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="text-gray-400">—</span>;
  const tone = v >= 65 ? 'bg-emerald-100 text-emerald-800'
    : v >= 45 ? 'bg-amber-100 text-amber-800'
    : 'bg-red-100 text-red-700';
  return <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${tone}`}>{v.toFixed(0)}%</span>;
}

function Table({ rows, tone, onPick, pick }: {
  rows: Row[]; tone: 'smooth' | 'volatile';
  onPick: (s: string) => void; pick: string | null;
}) {
  if (rows.length === 0) {
    return <div className="py-8 text-center text-sm text-gray-400">No names on this side of the split.</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-[10px] uppercase text-gray-500 border-b">
            <th className="py-1.5">Symbol</th>
            <th className="py-1.5 text-right" title="annualised volatility of its own price">Vol</th>
            <th className="py-1.5 text-right">1M</th>
            <th className="py-1.5 text-right">3M</th>
            <th className="py-1.5 text-right">12M</th>
            <th className="py-1.5 text-right" title="pullbacks of 5%+ from a 20-day high, per year">Dips/yr</th>
            <th className="py-1.5 text-right" title="median depth of those pullbacks">Depth</th>
            <th className="py-1.5 text-right" title="share of those dips that regained the prior high within 20 sessions">Recovers</th>
            <th className="py-1.5 text-right" title="average daily high-low range">Range</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol}
              onClick={() => onPick(r.symbol === pick ? '' : r.symbol)}
              className={`border-b border-gray-100 cursor-pointer hover:bg-gray-50 ${
                pick === r.symbol ? 'bg-blue-50' : ''}`}>
              <td className="py-1.5">
                <span className="font-medium">{r.symbol}</span>
                <span className="ml-1.5 text-[10px] text-gray-400">
                  {SEGMENT_LABEL[r.segment]?.replace(' (ex-50)', '') ?? r.segment}
                </span>
              </td>
              <td className="py-1.5 text-right font-semibold tabular-nums"
                style={{ color: tone === 'smooth' ? C.smooth : C.volatile }}>
                {r.vol_pct.toFixed(0)}%
              </td>
              <td className="py-1.5 text-right tabular-nums"><Ret v={r.ret_1m} /></td>
              <td className="py-1.5 text-right tabular-nums"><Ret v={r.ret_3m} /></td>
              <td className="py-1.5 text-right tabular-nums"><Ret v={r.ret_12m} /></td>
              <td className="py-1.5 text-right tabular-nums text-gray-600">{plain(r.dips_per_year, 1, '')}</td>
              <td className="py-1.5 text-right tabular-nums text-gray-600">{plain(r.median_dip_pct)}</td>
              <td className="py-1.5 text-right"><Recovery v={r.recovery_rate_pct} /></td>
              <td className="py-1.5 text-right tabular-nums text-gray-400">{plain(r.avg_daily_range_pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function RszPage() {
  const [cov, setCov] = useState<Coverage | null>(null);
  const [data, setData] = useState<Swing | null>(null);
  const [segment, setSegment] = useState('NIFTYMIDCAP150');
  const [minAligned, setMinAligned] = useState(3);
  const [limit, setLimit] = useState(10);
  const [pick, setPick] = useState<string | null>(null);
  const [hist, setHist] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/rsz/coverage').then((r) => r.json())
      .then(setCov).catch((e) => setErr(String(e))).finally(() => setLoading(false));
  }, []);

  const load = useCallback(() => {
    const q = new URLSearchParams({ segment, min_aligned: String(minAligned), limit: String(limit) });
    fetch(`/api/rsz/swing?${q}`).then((r) => r.json())
      .then((d) => { if (d.success) setData(d); else setErr(d.detail ?? 'failed'); })
      .catch((e) => setErr(String(e)));
  }, [segment, minAligned, limit]);

  useEffect(() => { if (cov?.ready) load(); }, [cov, load]);

  useEffect(() => {
    if (!pick) { setHist(null); return; }
    fetch(`/api/rsz/symbol/${pick}?days=400`).then((r) => r.json())
      .then((d) => { if (d.success) setHist(d); }).catch(() => setHist(null));
  }, [pick]);

  if (loading) return <div className="min-h-screen p-6 text-gray-500">Loading…</div>;
  if (err || !cov) {
    return <div className="min-h-screen p-6">
      <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
        Could not load RSZ: {err ?? 'unknown error'}
      </div></div>;
  }
  if (!cov.ready) {
    return <div className="min-h-screen p-6 space-y-4">
      <h1 className="text-xl font-bold">RSZ</h1>
      <Card><CardContent className="py-12 text-center text-gray-500">
        {cov.message}<div className="mt-2 font-mono text-xs">python scripts/compute_rsz.py</div>
      </CardContent></Card></div>;
  }

  const scatter = data ? [...data.smooth, ...data.volatile]
    .filter((r) => r.recovery_rate_pct != null && r.median_dip_pct != null)
    .map((r) => ({ ...r, x: r.vol_pct, y: r.recovery_rate_pct, z: r.median_dip_pct })) : [];

  return (
    <div className="min-h-screen p-6 max-w-[1500px] mx-auto space-y-4">
      {/* ── header ─────────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <Link href="/analytics" className="text-gray-500 hover:text-gray-800">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <Waves className="w-6 h-6 text-teal-600" />
          <div>
            <h1 className="text-xl font-bold text-gray-900">Strong stocks — smooth vs volatile</h1>
            <div className="flex items-center gap-2 text-xs text-gray-500 flex-wrap">
              <span>{cov.symbols} names · {cov.first_date} → {cov.latest_date}</span>
              <Badge variant="secondary">vs {cov.benchmark}</Badge>
              <span>horizons {cov.params.horizons.join('/')}d</span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select value={segment} onChange={(e) => setSegment(e.target.value)}
            className="rounded-md border border-gray-300 p-1.5 text-xs">
            {cov.segments.map((s) => (
              <option key={s.segment} value={s.segment}>
                {SEGMENT_LABEL[s.segment] ?? s.segment} ({s.names})
              </option>
            ))}
          </select>
          <div className="flex rounded-md border border-gray-300 overflow-hidden text-xs">
            {[1, 2, 3].map((a) => (
              <button key={a} onClick={() => setMinAligned(a)}
                className={`px-2 py-1.5 ${minAligned === a
                  ? 'bg-teal-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}>
                {a}/3+
              </button>
            ))}
          </div>
          <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}
            className="rounded-md border border-gray-300 p-1.5 text-xs">
            {[10, 20, 30, 40].map((n) => <option key={n} value={n}>top {n}</option>)}
          </select>
        </div>
      </div>

      {/* ── what this is ───────────────────────────────────────────────── */}
      <Card>
        <CardContent className="py-3 text-sm text-gray-600 flex gap-2">
          <Info className="w-4 h-4 text-gray-400 shrink-0 mt-0.5" />
          <div>
            Every name here is <span className="font-medium">already strong</span> — its
            relative strength vs {cov.benchmark} agrees on at least {minAligned} of the
            22 / 66 / 222-day horizons. The only thing being split is{' '}
            <span className="font-medium">how it moves</span>: smooth compounders on the
            left to hold, jumpy ones on the right whose dips are worth trading. The cut is
            the median volatility of the selected names, so it adapts to the segment.
            {' '}<span className="text-gray-400">This page does not rank — RSZ lost to a plain
            price return as a ranker, so ranking was removed from the strategy and
            simulator pages.</span>
          </div>
        </CardContent>
      </Card>

      {data?.message && (
        <Card><CardContent className="py-6 text-center text-sm text-gray-500">
          {data.message}
        </CardContent></Card>
      )}

      {data && !data.message && (
        <>
          <div className="flex items-center gap-3 text-xs text-gray-500 flex-wrap">
            <span>{data.date}</span>
            <span>· <span className="font-medium text-gray-700">{data.qualifying}</span> names
              qualify at {minAligned}/3, showing top {data.shown}</span>
            <span>· split at <span className="font-medium text-gray-700">{data.cut_vol_pct}%</span> volatility</span>
            <span>· “dip” = {data.dip_threshold_pct}%+ off a 20-day high,
              “recovers” = regained within {data.recovery_window} sessions</span>
          </div>

          {/* ── the two books ────────────────────────────────────────────── */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-base flex items-center gap-2">
                  <TrendingUp className="w-4 h-4" style={{ color: C.smooth }} />
                  Smooth — hold these
                  <span className="text-xs font-normal text-gray-500">
                    under {data.cut_vol_pct}% vol · {data.smooth.length} names
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <Table rows={data.smooth} tone="smooth" onPick={(s) => setPick(s || null)} pick={pick} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-base flex items-center gap-2">
                  <Activity className="w-4 h-4" style={{ color: C.volatile }} />
                  Volatile — trade the swings
                  <span className="text-xs font-normal text-gray-500">
                    over {data.cut_vol_pct}% vol · {data.volatile.length} names
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <Table rows={data.volatile} tone="volatile" onPick={(s) => setPick(s || null)} pick={pick} />
                <div className="mt-2 text-[11px] text-gray-500">
                  <span className="font-medium">Read the Recovers column first.</span> A name with
                  frequent, deep dips that usually recover is a buy-the-dip candidate; the same
                  dips with a low recovery rate mean the pullbacks are trend breaks, not
                  discounts.
                </div>
              </CardContent>
            </Card>
          </div>

          {/* ── the map ──────────────────────────────────────────────────── */}
          <Card>
            <CardHeader className="pb-1">
              <CardTitle className="text-base">
                Which dips get bought
                <span className="text-xs font-normal text-gray-500">
                  {' '}· volatility vs recovery rate · bubble = median dip depth
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <ScatterChart margin={{ top: 8, right: 16, bottom: 20, left: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke={C.grid} />
                    <XAxis type="number" dataKey="x" name="volatility" unit="%"
                      tick={{ fontSize: 10 }}
                      label={{ value: 'annualised volatility', position: 'insideBottom',
                        offset: -12, style: { fontSize: 10, fill: '#9ca3af' } }} />
                    <YAxis type="number" dataKey="y" name="recovers" unit="%" domain={[0, 100]}
                      tick={{ fontSize: 10 }}
                      label={{ value: 'dips that recover', angle: -90, position: 'insideLeft',
                        style: { fontSize: 10, fill: '#9ca3af' } }} />
                    <ZAxis type="number" dataKey="z" range={[60, 400]} name="median dip" unit="%" />
                    {data.cut_vol_pct && <ReferenceLine x={data.cut_vol_pct} stroke="#9ca3af" strokeDasharray="4 4" />}
                    <ReferenceLine y={50} stroke="#d1d5db" />
                    <Tooltip cursor={{ strokeDasharray: '3 3' }}
                      content={({ payload }) => {
                        if (!payload?.length) return null;
                        const r = payload[0].payload as Row;
                        return (
                          <div className="rounded border border-gray-200 bg-white p-2 text-xs shadow">
                            <div className="font-medium">{r.symbol}</div>
                            <div>vol {r.vol_pct.toFixed(0)}% · recovers {r.recovery_rate_pct?.toFixed(0)}%</div>
                            <div>median dip {r.median_dip_pct?.toFixed(1)}% · {r.dips_per_year?.toFixed(1)}/yr</div>
                            <div className="text-gray-500">12M {pct(r.ret_12m)}</div>
                          </div>
                        );
                      }} />
                    <Scatter data={scatter}>
                      {scatter.map((r, i) => (
                        <Cell key={i} fill={data.cut_vol_pct && r.vol_pct > data.cut_vol_pct
                          ? C.volatile : C.smooth} fillOpacity={0.75} />
                      ))}
                    </Scatter>
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
              <div className="text-[11px] text-gray-500 mt-1">
                Top-right is the sweet spot for swing trading: moves enough to be worth
                trading, and its pullbacks usually get bought. Bottom-right moves just as
                much but the dips keep going — those are the traps.
              </div>
            </CardContent>
          </Card>

          {/* ── drill-down ───────────────────────────────────────────────── */}
          {hist && (
            <Card>
              <CardHeader className="pb-1">
                <CardTitle className="text-base flex items-center gap-2">
                  <Search className="w-4 h-4 text-gray-500" />
                  {hist.symbol}
                  <span className="text-xs font-normal text-gray-500">
                    price (rebased to 100) vs its strength score · last {hist.count} sessions
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="h-64">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={hist.rows}>
                      <CartesianGrid strokeDasharray="3 3" stroke={C.grid} />
                      <XAxis dataKey="date" tick={{ fontSize: 10 }} minTickGap={60} />
                      <YAxis yAxisId="l" tick={{ fontSize: 10 }} />
                      <YAxis yAxisId="r" orientation="right" domain={[0, 100]} tick={{ fontSize: 10 }} />
                      <Tooltip formatter={(v: number) => v?.toFixed(1)} />
                      <Legend wrapperStyle={{ fontSize: 11 }} />
                      <Line yAxisId="l" type="monotone" dataKey="price_rebased" name="price (=100)"
                        stroke="#111827" dot={false} strokeWidth={2} />
                      <Line yAxisId="r" type="monotone" dataKey="rsz_score" name="strength score"
                        stroke={C.smooth} dot={false} strokeWidth={1.5} />
                      <Line yAxisId="r" type="monotone" dataKey="vol_pct" name="volatility %"
                        stroke={C.volatile} dot={false} strokeWidth={1} />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
