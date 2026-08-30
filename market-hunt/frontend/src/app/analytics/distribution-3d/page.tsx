'use client';

import React, { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';

const BACKEND = '/api';

interface MonthDist { date: string; n: number; counts: number[]; pct: number[]; mean: number; median: number; }
interface DistResp { universe: string; metric: string; n_members: number; bins: number; bin_edges: number[]; months: MonthDist[]; }

const UNIVERSES = [
  { value: 'NIFTY50', label: 'Nifty 50' }, { value: 'NIFTY100', label: 'Nifty 100' },
  { value: 'NIFTY200', label: 'Nifty 200' }, { value: 'NIFTY500', label: 'Nifty 500' },
  { value: 'NIFTYMIDCAP150', label: 'Midcap 150' }, { value: 'NIFTYSMALLCAP250', label: 'Smallcap 250' },
  { value: 'NIFTYMICROCAP250', label: 'Microcap 250' },
];
const METRICS = [
  { value: 'mean_long', label: 'mean_long (222d RS)' }, { value: 'mean_mid', label: 'mean_mid (66d RS)' },
  { value: 'mean_short', label: 'mean_short (22d RS)' }, { value: 'truevx_score', label: 'truevx_score (instant)' },
];
// The 4 cap tiers for the compare view (label shown above each surface).
const COMPARE = [
  { value: 'NIFTY100', label: 'Nifty 100 (large)' },
  { value: 'NIFTYMIDCAP150', label: 'Midcap 150' },
  { value: 'NIFTYSMALLCAP250', label: 'Smallcap 250' },
  { value: 'NIFTYMICROCAP250', label: 'Microcap 250' },
];

const COLORSCALE: [number, string][] = [
  [0, 'rgb(13,18,28)'], [0.33, 'rgb(120,90,30)'], [0.66, 'rgb(254,140,60)'], [1, 'rgb(255,60,56)'],
];
const VIEWS: Record<string, { eye: { x: number; y: number; z: number }; up: { x: number; y: number; z: number } }> = {
  Top: { eye: { x: 0, y: 0, z: 2.2 }, up: { x: 0, y: 1, z: 0 } },
  Front: { eye: { x: 0, y: -2.2, z: 0.2 }, up: { x: 0, y: 0, z: 1 } },
  Right: { eye: { x: 2.2, y: 0, z: 0.2 }, up: { x: 0, y: 0, z: 1 } },
  Iso: { eye: { x: 1.6, y: -1.6, z: 1.1 }, up: { x: 0, y: 0, z: 1 } },
};

function buildTrace(dist: DistResp) {
  const x = dist.months.map(m => m.date);
  const y = Array.from({ length: dist.bins }, (_, bi) => (dist.bin_edges[bi] + dist.bin_edges[bi + 1]) / 2);
  const z = Array.from({ length: dist.bins }, (_, bi) => dist.months.map(m => m.pct[bi]));
  return {
    type: 'surface', x, y, z, colorscale: COLORSCALE, showscale: false,
    hovertemplate: '%{x}<br>strength %{y:.0f}<br>%{z:.1f}%<extra></extra>',
  };
}
function buildLayout(height: number) {
  const ax = { color: '#8b94a7', gridcolor: 'rgba(255,255,255,0.08)', zerolinecolor: 'rgba(255,255,255,0.12)', backgroundcolor: 'rgba(0,0,0,0)', showbackground: true };
  return {
    autosize: true, height, margin: { l: 0, r: 0, t: 4, b: 0 },
    paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: '#c2c9d6' },
    scene: {
      bgcolor: 'rgba(0,0,0,0)',
      xaxis: { title: 'Time', ...ax }, yaxis: { title: 'Strength', ...ax }, zaxis: { title: '% univ', ...ax },
      camera: { ...VIEWS.Iso, projection: { type: 'orthographic' } },
      aspectmode: 'manual', aspectratio: { x: 2.2, y: 1, z: 0.9 },
    },
  };
}

export default function Distribution3DPage() {
  const [mode, setMode] = useState<'single' | 'compare'>('single');
  const [universe, setUniverse] = useState('NIFTY50');
  const [metric, setMetric] = useState('mean_long');
  const [start, setStart] = useState('2020-01-01');
  const [end, setEnd] = useState('2026-08-30');
  const [bins, setBins] = useState(20);
  const [pit, setPit] = useState(true);
  const [data, setData] = useState<DistResp | null>(null);
  const [comp, setComp] = useState<DistResp[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const plotly = useRef<any>(null);
  const divs = useRef<Record<string, HTMLDivElement | null>>({});
  const plotted = useRef<Set<string>>(new Set());

  const fetchDist = async (uni: string): Promise<DistResp> => {
    const qs = new URLSearchParams({ universe: uni, metric, start, end, bins: String(bins), point_in_time: String(pit) });
    const r = await fetch(`${BACKEND}/analytics/strength-distribution?${qs}`);
    if (!r.ok) throw new Error(`${uni}: HTTP ${r.status}`);
    return r.json();
  };

  const load = async () => {
    setLoading(true); setError(null);
    try {
      if (mode === 'single') { setData(await fetchDist(universe)); }
      else { setComp(await Promise.all(COMPARE.map(c => fetchDist(c.value)))); }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed to load');
      if (mode === 'single') setData(null); else setComp([]);
    } finally { setLoading(false); }
  };

  // reload whenever the mode flips (so switching to Compare fetches the 4 tiers)
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [mode]);

  const renderPlot = async (id: string, dist: DistResp, height: number) => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const mod: any = await import('plotly.js-dist-min');
    const P = mod.default || mod; plotly.current = P;
    const el = divs.current[id];
    if (!el || !dist || dist.months.length === 0) return;
    P.react(el, [buildTrace(dist)], buildLayout(height), { responsive: true, displaylogo: false });
    plotted.current.add(id);
  };

  useEffect(() => { if (mode === 'single' && data) renderPlot('single', data, 640); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [data, mode]);
  useEffect(() => { if (mode === 'compare') comp.forEach(d => renderPlot(d.universe, d, 380)); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [comp, mode]);

  const setView = (name: string) => {
    const P = plotly.current; if (!P) return;
    plotted.current.forEach(id => {
      const el = divs.current[id];
      if (el) P.relayout(el, { 'scene.camera': { ...VIEWS[name], projection: { type: 'orthographic' } } });
    });
  };

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-7xl mx-auto">
        <Link href="/analytics/distribution" className="flex items-center gap-2 text-blue-600 hover:text-blue-800 mb-4">
          <ArrowLeft className="h-5 w-5" /> 2D Signature
        </Link>
        <h1 className="text-3xl font-bold text-gray-900 mb-1">Universe Strength Signature — 3D</h1>
        <p className="text-gray-600 mb-6">
          RS distribution as a 3D surface: <b>X = time</b>, <b>Y = strength (0–100)</b>, <b>Z = % of universe</b>.
          Drag to orbit; the view presets move all surfaces together — <b>Top</b> = heatmap, <b>Front</b> = ridge over time,
          <b> Right</b> = overall shape.
        </p>

        <div className="bg-white rounded-lg border border-gray-200 p-4 mb-4 flex flex-wrap items-end gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Mode</label>
            <select value={mode} onChange={e => { plotted.current.clear(); setMode(e.target.value as 'single' | 'compare'); }} className="p-2 border border-gray-300 rounded text-sm">
              <option value="single">Single universe</option>
              <option value="compare">Compare 4 cap tiers</option>
            </select>
          </div>
          {mode === 'single' && (
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">Universe</label>
              <select value={universe} onChange={e => setUniverse(e.target.value)} className="p-2 border border-gray-300 rounded text-sm">
                {UNIVERSES.map(u => <option key={u.value} value={u.value}>{u.label}</option>)}
              </select>
            </div>
          )}
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Metric</label>
            <select value={metric} onChange={e => setMetric(e.target.value)} className="p-2 border border-gray-300 rounded text-sm">
              {METRICS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
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
          <button onClick={() => { plotted.current.clear(); load(); }} disabled={loading}
            className="px-4 py-2 bg-indigo-600 text-white rounded text-sm font-medium hover:bg-indigo-700 disabled:opacity-50">
            {loading ? 'Loading…' : 'Update'}
          </button>
          {loading && mode === 'compare' && <span className="text-sm text-amber-600">fetching 4 tiers (microcap is heavy)…</span>}
        </div>

        <div className="flex gap-2 mb-3">
          {Object.keys(VIEWS).map(v => (
            <button key={v} onClick={() => setView(v)}
              className="px-3 py-1.5 bg-gray-800 text-white rounded text-xs font-medium hover:bg-gray-700">{v}</button>
          ))}
        </div>

        {error && <div className="text-red-600 mb-4">Error: {error}</div>}

        {mode === 'single' ? (
          <div className="bg-white rounded-lg border border-gray-200 p-2">
            <div ref={el => { divs.current['single'] = el; }} style={{ width: '100%', height: 640 }} />
            {(!data || data.months.length === 0) && !loading && (
              <div className="text-gray-500 p-6 text-center">No data for this universe/range.</div>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {COMPARE.map(c => {
              const d = comp.find(x => x.universe === c.value);
              return (
                <div key={c.value} className="bg-white rounded-lg border border-gray-200 p-2">
                  <div className="flex items-center justify-between px-1 mb-1">
                    <span className="text-sm font-medium text-gray-700">{c.label}</span>
                    {d && <span className="text-xs text-gray-500">{d.n_members} members</span>}
                  </div>
                  <div ref={el => { divs.current[c.value] = el; }} style={{ width: '100%', height: 380 }} />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
