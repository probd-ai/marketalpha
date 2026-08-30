'use client';

import React, { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';

const BACKEND = '/api';

interface MonthDist { date: string; n: number; counts: number[]; pct: number[]; mean: number; median: number; }
interface DistAllResp { universe: string; n_members: number; bins: number; bin_edges: number[]; metrics: Record<string, MonthDist[]>; }

const UNIVERSES = [
  { value: 'NIFTY50', label: 'Nifty 50 (large)' },
  { value: 'NIFTY100', label: 'Nifty 100' },
  { value: 'NIFTY200', label: 'Nifty 200' },
  { value: 'NIFTY500', label: 'Nifty 500 (broad)' },
  { value: 'NIFTYMIDCAP150', label: 'Nifty Midcap 150' },
  { value: 'NIFTYSMALLCAP250', label: 'Nifty Smallcap 250' },
  { value: 'NIFTYMICROCAP250', label: 'Nifty Microcap 250' },
];
const METRICS = [
  { key: 'mean_long', label: 'mean_long (222d RS)' },
  { key: 'mean_mid', label: 'mean_mid (66d RS)' },
  { key: 'mean_short', label: 'mean_short (22d RS)' },
  { key: 'truevx_score', label: 'truevx_score (instant RS)' },
];

// Warm "heatmap" ramp: white (empty) -> light yellow -> orange -> bright red (densest).
// t in [0,1], interpolated across the 4 stops.
function ramp(t: number): string {
  const tt = Math.max(0, Math.min(1, t));
  // empty (low) blends into the dark panel; ridge glows yellow -> orange -> red
  const stops = [[18, 24, 38], [120, 90, 30], [254, 140, 60], [255, 60, 56]];
  const seg = stops.length - 1;          // 3 segments
  const x = tt * seg;
  const i = Math.min(seg - 1, Math.floor(x));
  const f = x - i;
  const a = stops[i], b = stops[i + 1];
  const c = a.map((v, k) => Math.round(v + (b[k] - v) * f));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

// Robust normalizer: scale by the 97th-percentile cell (so one hot cell doesn't wash
// everything out), then gamma<1 to expand contrast among the many low-density cells.
function colorScaler(rows: MonthDist[]): (pct: number) => string {
  const all: number[] = [];
  for (const r of rows) for (const p of r.pct) all.push(p);
  all.sort((a, b) => a - b);
  const p97 = all.length ? all[Math.floor(0.97 * (all.length - 1))] : 1;
  const denom = p97 > 0 ? p97 : (all[all.length - 1] || 1);
  const GAMMA = 0.6;
  return (pct: number) => ramp(Math.pow(Math.min(1, pct / denom), GAMMA));
}

function Heatmap({ label, rows, binEdges, bins, selIdx, onSelect }:
  { label: string; rows: MonthDist[]; binEdges: number[]; bins: number; selIdx: number | null; onSelect: (i: number) => void }) {
  const color = useMemo(() => colorScaler(rows), [rows]);
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-3">
      <div className="text-sm font-medium text-gray-700 mb-2">{label}</div>
      <div className="flex">
        <div className="flex flex-col justify-between text-[9px] text-gray-500 pr-1" style={{ height: bins * 12 }}>
          {[100, 75, 50, 25, 0].map(v => <div key={v}>{v}</div>)}
        </div>
        <div className="flex-1 min-w-0">
          {/* Fluid columns: every month shares the panel width (flex-1) — no horizontal scroll. */}
          <div className="flex w-full">
            {rows.map((mo, ci) => (
              <div key={mo.date} onClick={() => onSelect(ci)}
                className={`flex flex-col flex-1 min-w-0 cursor-pointer ${selIdx === ci ? 'ring-2 ring-indigo-500 z-10' : ''}`}
                title={`${mo.date} · n=${mo.n} · mean=${mo.mean}`}>
                {[...Array(bins).keys()].reverse().map(bi => (
                  <div key={bi} style={{ height: 12, backgroundColor: color(mo.pct[bi]) }}
                    title={`${mo.date} · ${binEdges[bi]}–${binEdges[bi + 1]}: ${mo.pct[bi]}%`} />
                ))}
              </div>
            ))}
          </div>
          <div className="flex w-full text-[9px] text-gray-500 mt-1">
            {rows.map(mo => (
              <div key={mo.date} className="flex-1 min-w-0 overflow-visible whitespace-nowrap">
                {mo.date.endsWith('-01') ? mo.date.slice(2, 4) : ''}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function MiniHist({ label, row, binEdges, bins }:
  { label: string; row: MonthDist; binEdges: number[]; bins: number }) {
  const mx = useMemo(() => Math.max(...row.pct, 1), [row]);
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="text-xs font-medium text-gray-700">{label}</div>
        <div className="text-[10px] text-gray-500">n={row.n} · μ={row.mean} · md={row.median}</div>
      </div>
      <div className="space-y-0.5">
        {[...Array(bins).keys()].reverse().map(bi => (
          <div key={bi} className="flex items-center gap-1 text-[10px]">
            <span className="w-12 text-right text-gray-400">{binEdges[bi]}</span>
            <div className="flex-1 bg-gray-100 rounded h-3">
              <div className="h-3 rounded bg-red-500" style={{ width: `${(row.pct[bi] / mx) * 100}%` }} />
            </div>
            <span className="w-9 text-gray-600">{row.pct[bi]}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function DistributionPage() {
  const [universe, setUniverse] = useState('NIFTY50');
  const [start, setStart] = useState('2020-01-01');
  const [end, setEnd] = useState('2026-08-30');
  const [bins, setBins] = useState(20);
  const [pit, setPit] = useState(true);   // point-in-time (survivorship-bias-free) membership
  const [data, setData] = useState<DistAllResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<number | null>(null);
  // Long-horizon (computed SMA 222/444/666 of truevx_score) — loaded on demand (heavier).
  const [longData, setLongData] = useState<DistAllResp | null>(null);
  const [longLoading, setLongLoading] = useState(false);
  const [longErr, setLongErr] = useState<string | null>(null);
  const [longSel, setLongSel] = useState<number | null>(null);

  const loadLong = async () => {
    setLongLoading(true); setLongErr(null);
    try {
      const qs = new URLSearchParams({ universe, start, end, bins: String(bins), windows: '222,444,666', point_in_time: String(pit) });
      const r = await fetch(`${BACKEND}/analytics/strength-distribution-long?${qs}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j: DistAllResp = await r.json();
      setLongData(j);
      const len = j.metrics.sma_222?.length || 0;
      setLongSel(len ? len - 1 : null);
    } catch (e) {
      setLongErr(e instanceof Error ? e.message : 'failed to load');
      setLongData(null);
    } finally {
      setLongLoading(false);
    }
  };

  const load = async () => {
    setLoading(true); setError(null);
    try {
      const qs = new URLSearchParams({ universe, start, end, bins: String(bins), point_in_time: String(pit) });
      const r = await fetch(`${BACKEND}/analytics/strength-distribution-all?${qs}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j: DistAllResp = await r.json();
      setData(j);
      const len = j.metrics.mean_long?.length || 0;
      setSel(len ? len - 1 : null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed to load');
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const months = data?.metrics.mean_long || [];
  const selDate = data && sel !== null && months[sel] ? months[sel].date : null;

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-7xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <Link href="/analytics" className="flex items-center gap-2 text-blue-600 hover:text-blue-800">
            <ArrowLeft className="h-5 w-5" /> Analytics Hub
          </Link>
          <Link href="/analytics/distribution-3d" className="px-3 py-1.5 bg-gray-800 text-white rounded text-xs font-medium hover:bg-gray-700">
            View in 3D →
          </Link>
        </div>
        <h1 className="text-3xl font-bold text-gray-900 mb-1">Universe Strength Signature</h1>
        <p className="text-gray-600 mb-6">
          How a universe&apos;s TrueVX relative-strength distribution (0–100, vs Nifty 50) evolves over time — the
          cap-tier fingerprint. All four metrics shown together; X = month, Y = 0–100 bins, darker = larger share
          of the universe in that bin. Click any month to read its histograms below.
        </p>

        {/* Controls */}
        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-6 flex flex-wrap items-end gap-4">
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
            <input type="date" value={end} onChange={e => setEnd(e.target.value)} className="p-2 border border-gray-300 rounded text-sm" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Bins</label>
            <select value={bins} onChange={e => setBins(parseInt(e.target.value))} className="p-2 border border-gray-300 rounded text-sm">
              {[10, 20, 25, 50].map(b => <option key={b} value={b}>{b}</option>)}
            </select>
          </div>
          <label className="flex items-center gap-2 text-sm text-gray-600 select-none cursor-pointer" title="Survivorship-bias-free: use each month's actual index members">
            <input type="checkbox" checked={pit} onChange={e => setPit(e.target.checked)} className="accent-cyan-400" />
            Point-in-time
          </label>
          <button onClick={load} disabled={loading}
            className="px-4 py-2 bg-indigo-600 text-white rounded text-sm font-medium hover:bg-indigo-700 disabled:opacity-50">
            {loading ? 'Loading…' : 'Update'}
          </button>
          {data && <span className="text-sm text-gray-500">{data.n_members} {pit ? 'ever-members' : 'current members'} · {months.length} months</span>}
          {loading && <span className="text-sm text-amber-600">large universes take a few seconds…</span>}
        </div>

        {error && <div className="text-red-600 mb-4">Error: {error}</div>}

        {data && months.length > 0 && (
          <>
            {/* 2x2 heatmaps */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
              {METRICS.map(m => (
                <Heatmap key={m.key} label={m.label} rows={data.metrics[m.key]} binEdges={data.bin_edges}
                  bins={data.bins} selIdx={sel} onSelect={setSel} />
              ))}
            </div>

            {/* selected-month histograms */}
            {selDate && (
              <>
                <div className="text-sm font-medium text-gray-700 mb-2">Histograms — {selDate}</div>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                  {METRICS.map(m => {
                    const row = data.metrics[m.key][sel as number];
                    return row ? <MiniHist key={m.key} label={m.label} row={row} binEdges={data.bin_edges} bins={data.bins} /> : null;
                  })}
                </div>
              </>
            )}
          </>
        )}

        {data && months.length === 0 && !loading && (
          <div className="text-gray-500">No indicator data for this universe/range.</div>
        )}

        {/* Long-horizon RS state: computed SMAs (222 / 444 / 666) of truevx_score */}
        <div className="mt-8 pt-6 border-t border-gray-200">
          <div className="flex items-center gap-3 mb-1">
            <h2 className="text-lg font-semibold text-gray-900">Long-horizon RS state (SMA 222 / 444 / 666)</h2>
            <button onClick={loadLong} disabled={longLoading}
              className="px-3 py-1.5 bg-gray-800 text-white rounded text-xs font-medium hover:bg-gray-700 disabled:opacity-50">
              {longLoading ? 'Computing…' : longData ? 'Recompute' : 'Compute & show'}
            </button>
            {longLoading && <span className="text-xs text-amber-600">rolling 2–3yr SMAs — a few seconds…</span>}
          </div>
          <p className="text-xs text-gray-500 mb-4">
            Even slower smoothings of the RS composite (≈1yr / 2yr / 3yr). Lower variance, higher persistence —
            the cleanest regime state. 666d only becomes valid ~2020-09 (needs ~3yr of prior history).
          </p>
          {longErr && <div className="text-red-600 mb-3">Error: {longErr}</div>}
          {longData && (() => {
            const lmonths = longData.metrics.sma_222 || [];
            const lsel = longSel !== null && lmonths[longSel] ? longSel : null;
            const LM = [
              { key: 'sma_222', label: 'SMA 222 (≈1yr)' },
              { key: 'sma_444', label: 'SMA 444 (≈2yr)' },
              { key: 'sma_666', label: 'SMA 666 (≈3yr)' },
            ];
            return (
              <>
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
                  {LM.map(m => longData.metrics[m.key] ? (
                    <Heatmap key={m.key} label={m.label} rows={longData.metrics[m.key]} binEdges={longData.bin_edges}
                      bins={longData.bins} selIdx={longSel} onSelect={setLongSel} />
                  ) : null)}
                </div>
                {lsel !== null && (
                  <>
                    <div className="text-sm font-medium text-gray-700 mb-2">Histograms — {lmonths[lsel].date}</div>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                      {LM.map(m => {
                        const row = longData.metrics[m.key]?.[lsel];
                        return row ? <MiniHist key={m.key} label={m.label} row={row} binEdges={longData.bin_edges} bins={longData.bins} /> : null;
                      })}
                    </div>
                  </>
                )}
              </>
            );
          })()}
        </div>
      </div>
    </div>
  );
}
