'use client';

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';

const BACKEND = '/api';

interface MonthDist { date: string; n: number; counts: number[]; pct: number[]; mean: number; median: number; pct_pos: number; }
interface Resp { universe: string; n_members: number; point_in_time: boolean; start: string; end: string; bins: number; range_min: number; range_max: number; bin_edges: number[]; months: MonthDist[]; }

const UNIVERSES = [
  { value: 'NIFTY50', label: 'Nifty 50' }, { value: 'NIFTY100', label: 'Nifty 100' },
  { value: 'NIFTY200', label: 'Nifty 200' }, { value: 'NIFTY500', label: 'Nifty 500' },
  { value: 'NIFTYMIDCAP150', label: 'Midcap 150' }, { value: 'NIFTYSMALLCAP250', label: 'Smallcap 250' },
  { value: 'NIFTYMICROCAP250', label: 'Microcap 250' },
];

// warm density ramp on dark (empty blends into panel -> ridge glows yellow->orange->red)
function ramp(t: number): string {
  const tt = Math.max(0, Math.min(1, t));
  const stops = [[18, 24, 38], [120, 90, 30], [254, 140, 60], [255, 60, 56]];
  const seg = stops.length - 1; const x = tt * seg;
  const i = Math.min(seg - 1, Math.floor(x)); const f = x - i;
  const a = stops[i], b = stops[i + 1];
  const c = a.map((v, k) => Math.round(v + (b[k] - v) * f));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
function scaler(rows: MonthDist[]) {
  const all: number[] = [];
  for (const r of rows) for (const p of r.pct) all.push(p);
  all.sort((a, b) => a - b);
  const denom = all.length ? (all[Math.floor(0.97 * (all.length - 1))] || all[all.length - 1] || 1) : 1;
  return (pct: number) => ramp(Math.pow(Math.min(1, pct / (denom || 1)), 0.6));
}

const CELL = 12;

function Heatmap({ data, selIdx, onSelect }: { data: Resp; selIdx: number | null; onSelect: (i: number) => void }) {
  const rows = data.months;
  const color = useMemo(() => scaler(rows), [rows]);
  const span = data.range_max - data.range_min;
  const zeroFracFromTop = 1 - (0 - data.range_min) / span;          // where 0% sits
  const inRange = zeroFracFromTop >= 0 && zeroFracFromTop <= 1;
  const H = data.bins * CELL;
  const yTicks = [0, 0.25, 0.5, 0.75, 1].map(f => ({ f, v: Math.round(data.range_max - f * span) }));
  return (
    <div className="bg-white rounded-xl glass shadow-md p-4">
      <div className="text-sm font-medium text-gray-200 mb-2">
        Cumulative return % since {data.start} — % of universe per bin
      </div>
      <div className="flex">
        <div className="relative text-[10px] text-gray-500 pr-1" style={{ height: H, width: 38 }}>
          {yTicks.map(t => (
            <div key={t.f} className="absolute right-1" style={{ top: t.f * H - 6 }}>{t.v}%</div>
          ))}
          {inRange && <div className="absolute right-1 text-cyan-400 font-medium" style={{ top: zeroFracFromTop * H - 6 }}>0%</div>}
        </div>
        <div className="flex-1 min-w-0 relative">
          <div className="flex w-full">
            {rows.map((mo, ci) => (
              <div key={mo.date} onClick={() => onSelect(ci)}
                className={`flex flex-col flex-1 min-w-0 cursor-pointer ${selIdx === ci ? 'ring-2 ring-cyan-400 z-10' : ''}`}
                title={`${mo.date} · n=${mo.n} · median ${mo.median}% · ${mo.pct_pos}% positive`}>
                {[...Array(data.bins).keys()].reverse().map(bi => (
                  <div key={bi} style={{ height: CELL, backgroundColor: color(mo.pct[bi]) }}
                    title={`${mo.date} · ${data.bin_edges[bi]}–${data.bin_edges[bi + 1]}%: ${mo.pct[bi]}%`} />
                ))}
              </div>
            ))}
          </div>
          {/* 0% breakeven line */}
          {inRange && <div className="absolute left-0 right-0 pointer-events-none" style={{ top: zeroFracFromTop * H, borderTop: '1px dashed rgba(34,211,238,0.7)' }} />}
          <div className="flex w-full text-[9px] text-gray-500 mt-1">
            {rows.map(mo => (
              <div key={mo.date} className="flex-1 min-w-0 overflow-visible whitespace-nowrap">
                {mo.date.endsWith('-01') ? mo.date.slice(2, 4) : ''}
              </div>
            ))}
          </div>
        </div>
      </div>
      <div className="text-xs text-gray-500 mt-2">Dashed cyan line = 0% (breakeven). Click a month for its histogram.</div>
    </div>
  );
}

function Hist({ data, sel }: { data: Resp; sel: number }) {
  const row = data.months[sel];
  const mx = Math.max(...row.pct, 1);
  return (
    <div className="bg-white rounded-xl glass shadow-md p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm font-medium text-gray-200">Histogram — {row.date}</div>
        <div className="text-[11px] text-gray-500 mono">n={row.n} · median {row.median}% · mean {row.mean}% · {row.pct_pos}% positive</div>
      </div>
      <div className="space-y-0.5">
        {[...Array(data.bins).keys()].reverse().map(bi => {
          const neg = data.bin_edges[bi + 1] <= 0;
          return (
            <div key={bi} className="flex items-center gap-2 text-[11px]">
              <span className="w-16 text-right text-gray-400 mono">{data.bin_edges[bi]}%</span>
              <div className="flex-1 bg-gray-100 rounded h-4">
                <div className="h-4 rounded" style={{ width: `${(row.pct[bi] / mx) * 100}%`, backgroundColor: neg ? '#64748b' : '#ef4444' }} />
              </div>
              <span className="w-12 text-gray-300 mono">{row.pct[bi]}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function PriceDistributionPage() {
  const [universe, setUniverse] = useState('NIFTY50');
  const [start, setStart] = useState('2020-01-01');
  const [bins, setBins] = useState(20);
  const [rmin, setRmin] = useState(-80);
  const [rmax, setRmax] = useState(400);
  const [pit, setPit] = useState(true);
  const [data, setData] = useState<Resp | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<number | null>(null);

  const load = async () => {
    setLoading(true); setError(null);
    try {
      const qs = new URLSearchParams({ universe, start, bins: String(bins), range_min: String(rmin), range_max: String(rmax), point_in_time: String(pit) });
      const r = await fetch(`${BACKEND}/analytics/price-return-distribution?${qs}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j: Resp = await r.json();
      setData(j); setSel(j.months.length ? j.months.length - 1 : null);
    } catch (e) { setError(e instanceof Error ? e.message : 'failed'); setData(null); }
    finally { setLoading(false); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-7xl mx-auto">
        <Link href="/analytics" className="flex items-center gap-2 text-blue-600 hover:text-blue-800 mb-4">
          <ArrowLeft className="h-5 w-5" /> Analytics Hub
        </Link>
        <h1 className="text-3xl font-bold text-gray-900 mb-1">Universe Return Distribution</h1>
        <p className="text-gray-600 mb-6">
          Cross-sectional distribution of each stock&apos;s <b>cumulative price return % since the start date</b>,
          evolving monthly to today — universe-wise, point-in-time. Watch the spread fan out: median, dispersion,
          and the share of the universe above 0% (breadth).
        </p>

        <div className="bg-white rounded-xl glass p-4 mb-6 flex flex-wrap items-end gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Universe</label>
            <select value={universe} onChange={e => setUniverse(e.target.value)} className="p-2 border border-gray-300 rounded text-sm">
              {UNIVERSES.map(u => <option key={u.value} value={u.value}>{u.label}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Start</label>
            <input type="date" value={start} onChange={e => setStart(e.target.value)} className="p-2 border border-gray-300 rounded text-sm" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">End</label>
            <input type="text" value={data?.end || 'today'} disabled className="p-2 border border-gray-300 rounded text-sm w-28 opacity-70" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Return min %</label>
            <input type="number" value={rmin} onChange={e => setRmin(parseFloat(e.target.value))} className="p-2 border border-gray-300 rounded text-sm w-24" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Return max %</label>
            <input type="number" value={rmax} onChange={e => setRmax(parseFloat(e.target.value))} className="p-2 border border-gray-300 rounded text-sm w-24" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Bins</label>
            <select value={bins} onChange={e => setBins(parseInt(e.target.value))} className="p-2 border border-gray-300 rounded text-sm">
              {[10, 20, 25, 40, 50].map(b => <option key={b} value={b}>{b}</option>)}
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-600 select-none cursor-pointer" title="Survivorship-bias-free: each month's actual index members">
            <input type="checkbox" checked={pit} onChange={e => setPit(e.target.checked)} className="accent-cyan-400" /> Point-in-time
          </label>
          <button onClick={load} disabled={loading} className="px-4 py-2 bg-indigo-600 text-white rounded text-sm font-medium hover:bg-indigo-700 disabled:opacity-50">
            {loading ? 'Loading…' : 'Update'}
          </button>
          {data && <span className="text-sm text-gray-500 mono">{data.n_members} {pit ? 'ever-members' : 'current'} · {data.months.length} mo</span>}
          {loading && <span className="text-sm text-amber-600">large universes take a few seconds…</span>}
        </div>

        {error && <div className="text-red-600 mb-4">Error: {error}</div>}

        {data && data.months.length > 0 && (
          <div className="space-y-6">
            <Heatmap data={data} selIdx={sel} onSelect={setSel} />
            {sel !== null && data.months[sel] && <Hist data={data} sel={sel} />}
          </div>
        )}
        {data && data.months.length === 0 && !loading && <div className="text-gray-500">No price data for this universe/range.</div>}
      </div>
    </div>
  );
}
