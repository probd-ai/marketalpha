'use client';

/**
 * The Living Shape of mean_long — precomputed research study served from
 * GET /api/analytics/research/meanlong-shape (research_results collection).
 * Nothing is computed here; re-run scripts/research_meanlong/meanlong_shape.py
 * after a data refresh to update the study.
 */
import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  ResponsiveContainer, BarChart, Bar, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, Legend, ReferenceLine, Cell,
} from 'recharts';

// Validated dark-surface palette (dataviz method; surface #0c111b, ΔE 66)
const C = {
  rise: '#3987e5',   // blue — rising / positive
  fall: '#e66767',   // red — falling / negative
  grid: 'rgba(255,255,255,0.08)',
  ink: '#e5e7eb',
  inkDim: '#9ca3af',
};

interface Study {
  generated_at: string;
  window: string;
  summary: {
    n_symbols: number;
    velocity: Record<string, number>;
    occupancy_pct_by_decile: Record<string, number>;
    phase_up: Record<string, number>;
    phase_down: Record<string, number>;
    crosses: Record<string, Record<string, number>>;
    zone_forward: Record<string, Record<string, number>>;
    leadlag_corr: Record<string, number>;
    range: Record<string, number>;
    histograms: Record<string, { bin: string; lo: number; n: number }[]>;
  };
}

const tooltipStyle = {
  backgroundColor: '#111827', border: '1px solid rgba(255,255,255,0.15)',
  borderRadius: 8, color: C.ink, fontSize: 12,
};

function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}

/** Diverging cell shade for relative returns: red negative, blue positive, neutral near zero. */
function divergingCell(v: number | null | undefined): React.CSSProperties {
  if (v === null || v === undefined) return {};
  const a = Math.min(Math.abs(v) / 0.08, 1) * 0.45; // saturate at ±8%
  if (Math.abs(v) < 0.005) return { backgroundColor: 'rgba(156,163,175,0.10)' };
  return { backgroundColor: v > 0 ? `rgba(57,135,229,${a})` : `rgba(230,103,103,${a})` };
}

function StatTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl glass shadow-md p-4">
      <div className="text-xs uppercase tracking-wide text-gray-400">{label}</div>
      <div className="text-3xl font-semibold text-gray-100 mt-1">{value}</div>
      {sub && <div className="text-xs text-gray-400 mt-1">{sub}</div>}
    </div>
  );
}

function Section({ n, title, children }: { n: string; title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl glass shadow-md p-6 space-y-4">
      <h2 className="text-lg font-semibold text-gray-100">
        <span className="text-cyan-400 mr-2">{n}</span>{title}
      </h2>
      {children}
    </section>
  );
}

function Prose({ children }: { children: React.ReactNode }) {
  return <div className="text-sm leading-relaxed text-gray-300 space-y-2">{children}</div>;
}

export default function MeanLongShapePage() {
  const [study, setStudy] = useState<Study | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/analytics/research/meanlong-shape')
      .then(r => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then(setStudy)
      .catch(e => setError(String(e)));
  }, []);

  if (error) {
    return (
      <div className="p-8 text-gray-300">
        <p className="text-red-400">Study not available: {error}</p>
        <p className="mt-2 text-sm">Run <code className="text-cyan-300">scripts/research_meanlong/meanlong_shape.py</code> to (re)compute it.</p>
      </div>
    );
  }
  if (!study) return <div className="p-8 text-gray-400">Loading study…</div>;

  const s = study.summary;
  const occData = Object.entries(s.occupancy_pct_by_decile).map(([zone, p]) => ({ zone, pct: p }));
  const leadlagData = Object.entries(s.leadlag_corr)
    .map(([lag, corr]) => ({ lag: Number(lag), corr }))
    .sort((a, b) => a.lag - b.lag);
  const phaseDays = (s.histograms?.phase_days_up ?? []).map((b, i) => ({
    bin: b.bin.replace('-2000', '+'),
    rising: b.n,
    falling: s.histograms.phase_days_down[i]?.n ?? 0,
  }));

  const zoneLabels: Record<string, string> = {
    z0: '0–20', z1: '20–40', z2: '40–60', z3: '60–80', z4: '80–100',
  };
  const zoneRows = ['z0', 'z1', 'z2', 'z3', 'z4'].flatMap(z => (['up', 'down'] as const).map(d => {
    const r = s.zone_forward[`${z}_${d}`] || {};
    return {
      key: `${z}_${d}`, zone: zoneLabels[z], dir: d,
      n: r.n, abs63: r.abs63_p50, rel63: r.rel63_p50, abs252: r.abs252_p50, rel252: r.rel252_p50,
    };
  }));

  const crossOrder = ['20_up', '40_up', '50_up', '60_up', '80_up', '20_down', '40_down', '50_down', '60_down', '80_down'];
  const crossRows = crossOrder.filter(k => s.crosses[k]).map(k => {
    const r = s.crosses[k];
    const [lv, d] = k.split('_');
    return {
      key: k, label: `${lv} ${d === 'up' ? '↑' : '↓'}`, dir: d, n: r.n,
      rel63: r.rel63_p50, rel252: r.rel252_p50, hit252: r.abs252_hit,
    };
  });

  return (
    <div className="p-6 md:p-8 max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="space-y-2">
        <Link href="/analytics" className="text-xs text-cyan-400 hover:underline">← Analytics</Link>
        <h1 className="text-2xl font-bold text-gray-100">The Living Shape of <code className="text-cyan-300">mean_long</code></h1>
        <p className="text-sm text-gray-400">
          Full-universe characterization of TrueVX <code>mean_long</code> (SMA₂₂₂ of the 0–100 relative-strength
          composite vs Nifty 50) — movement physics, phase grammar, and what each state means for forward returns.
        </p>
        <p className="text-xs text-gray-500">
          {s.n_symbols} stocks · {study.window} · computed {new Date(study.generated_at).toLocaleString()} ·
          precomputed offline, served from <code>research_results</code>
        </p>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatTile label="Hard speed cap" value="0.45/day" sub="100/222 — one SMA slot; observed p99 = 0.40" />
        <StatTile label="Median drift" value={`${s.velocity.daily_abs_p50_median_across_stocks}/day`} sub="20→80 traverse ≈ 500 trading days at this pace" />
        <StatTile label="Median phase" value={`${s.phase_up.days_p50} days`} sub={`p90 ${s.phase_up.days_p90}d — moves come in quarters`} />
        <StatTile label="Never complete 20→80" value={`${s.range.stocks_with_zero_trips_pct}%`} sub="of stocks, in 8.5 years — round trips are rare" />
      </div>

      {/* 1. Physics */}
      <Section n="§1" title="Physics — the finite boundaries are speed, not just level">
        <Prose>
          <p>
            Because <code>mean_long</code> is a 222-day average of a bounded 0–100 score, it has a hard
            velocity ceiling: swapping one day in the window can move it at most <b>100/222 ≈ 0.45 points/day</b>.
            The data confirms the ceiling is real — the 99th-percentile daily step is 0.40 and the maximum
            observed step sits exactly at the cap. Typical drift is far slower: <b>0.118/day median</b>.
          </p>
          <p>
            Practical consequence: a zone change is a <b>multi-month commitment</b>. Crossing from 20 to 80
            cannot happen in fewer than ~133 trading days even at maximum theoretical speed; at the observed
            sustained fast pace (p95 of 21-day velocity ≈ 6 points) it takes ~215 days, and at median drift
            roughly two years. The indicator cannot spike, cannot gap, cannot whipsaw daily — every state it
            reaches was telegraphed for weeks.
          </p>
        </Prose>
        <h3 className="text-sm font-medium text-gray-200 mt-2">Where it lives — occupancy by decile (% of all stock-days)</h3>
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={occData} margin={{ top: 12, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid stroke={C.grid} vertical={false} />
            <XAxis dataKey="zone" tick={{ fill: C.inkDim, fontSize: 11 }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: C.inkDim, fontSize: 11 }} axisLine={false} tickLine={false} unit="%" />
            <Tooltip contentStyle={tooltipStyle} formatter={(v) => [`${v}%`, 'share of days']} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
            <Bar dataKey="pct" fill={C.rise} radius={[4, 4, 0, 0]} maxBarSize={48} />
          </BarChart>
        </ResponsiveContainer>
        <Prose>
          <p>
            The distribution is a broad hump over 30–80. True extremes are rare events: under 0.2% of all
            stock-days sit below 10, and under 5% above 90. The effective playing field is roughly
            <b> 20–90</b>, and the median stock uses a lifetime span of only {s.range.span_p50?.toFixed(0)} points.
            <b> {s.range.stocks_with_zero_trips_pct}% of stocks never completed a single 20→80 trip</b> in the
            whole sample — full-range round trips are scarce per name, which means wave-style strategies must
            harvest <i>breadth across the universe</i>, not signal frequency per stock.
          </p>
        </Prose>
      </Section>

      {/* 2. Phases */}
      <Section n="§2" title="Movement grammar — sixty-day pushes, year-long marches">
        <h3 className="text-sm font-medium text-gray-200">Phase duration distribution (trading days, 21d-slope segments)</h3>
        <ResponsiveContainer width="100%" height={240}>
          <BarChart data={phaseDays} margin={{ top: 12, right: 8, left: -16, bottom: 0 }} barGap={2}>
            <CartesianGrid stroke={C.grid} vertical={false} />
            <XAxis dataKey="bin" tick={{ fill: C.inkDim, fontSize: 11 }} axisLine={false} tickLine={false} />
            <YAxis tick={{ fill: C.inkDim, fontSize: 11 }} axisLine={false} tickLine={false} />
            <Tooltip contentStyle={tooltipStyle} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
            <Legend wrapperStyle={{ fontSize: 12, color: C.inkDim }} />
            <Bar dataKey="rising" name="Rising phases" fill={C.rise} radius={[4, 4, 0, 0]} maxBarSize={32} />
            <Bar dataKey="falling" name="Falling phases" fill={C.fall} radius={[4, 4, 0, 0]} maxBarSize={32} />
          </BarChart>
        </ResponsiveContainer>
        <Prose>
          <p>
            The shape moves in <b>directional phases with a median length of ~{s.phase_up.days_p50} trading days
            and median amplitude of ±4 points</b>; the 90th percentile stretches to ~{s.phase_up.days_p90} days
            and ±38 points. Up and down phases are strikingly symmetric — the pendulum swings both ways with the
            same rhythm.
          </p>
          <p>
            <b>The counterintuitive finding:</b> during <i>rising</i> phases, the median stock&apos;s concurrent
            relative return is <b className="text-red-300">negative ({pct(s.phase_up.rel_ret_p50)})</b>, positive in
            only {pct(s.phase_up.rel_ret_pos_share, 1)} of phases — while during <i>falling</i> phases it is
            <b className="text-blue-300"> positive ({pct(s.phase_down.rel_ret_p50)})</b>. This is not a paradox;
            it is the SMA lag made visible. <code>mean_long</code> keeps climbing <i>after</i> the outperformance
            that caused the climb has already faded, and keeps falling after the damage is done. Concurrent
            indicator direction is a <b>rear-view mirror</b> — never read it as a same-day signal.
          </p>
        </Prose>
      </Section>

      {/* 3. Lead/lag */}
      <Section n="§3" title="Lead/lag — price moves first, the indicator confirms">
        <h3 className="text-sm font-medium text-gray-200">
          Correlation of Δ₂₁ mean_long with 21-day relative return, by lag (negative lag = return happened first)
        </h3>
        <ResponsiveContainer width="100%" height={240}>
          <LineChart data={leadlagData} margin={{ top: 12, right: 16, left: -16, bottom: 0 }}>
            <CartesianGrid stroke={C.grid} vertical={false} />
            <XAxis dataKey="lag" tick={{ fill: C.inkDim, fontSize: 11 }} axisLine={false} tickLine={false}
                   label={{ value: 'lag (days)', position: 'insideBottomRight', fill: C.inkDim, fontSize: 11 }} />
            <YAxis tick={{ fill: C.inkDim, fontSize: 11 }} axisLine={false} tickLine={false} domain={[0, 0.4]} />
            <ReferenceLine x={0} stroke="rgba(255,255,255,0.25)" strokeDasharray="4 4" />
            <Tooltip contentStyle={tooltipStyle} labelFormatter={(l) => `lag ${l}d`} />
            <Line type="monotone" dataKey="corr" stroke={C.rise} strokeWidth={2} dot={{ r: 3, fill: C.rise }} />
          </LineChart>
        </ResponsiveContainer>
        <Prose>
          <p>
            Correlation peaks at <b>0.36 at lag −21</b>: the indicator&apos;s move lines up best with the relative
            return that finished <b>a month earlier</b>. At every positive lag the correlation collapses to
            ≈0.03 — an indicator move carries almost no <i>incremental</i> linear information about next
            month&apos;s relative return.
          </p>
          <p>
            <b>What mean_long is, then, is a regime confirmer.</b> Its forward value does not come from fresh
            information but from <i>state persistence</i>: phases last a median of two months and a tail of a
            year, so once a state is confirmed it tends to stay confirmed. Every entry taken on a mean_long
            signal pays a structural lag tax of roughly a month of the move — the compensation is that the
            regime you are joining usually has months left to run.
          </p>
        </Prose>
      </Section>

      {/* 4. Forward map */}
      <Section n="§4" title="The tradable map — direction beats level, everywhere">
        <h3 className="text-sm font-medium text-gray-200">Median forward returns by zone × direction (vs Nifty 50 where marked “rel”)</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-gray-300">
            <thead>
              <tr className="text-xs text-gray-400 border-b border-white/10">
                <th className="text-left py-2 pr-4">Zone</th>
                <th className="text-left pr-4">Direction</th>
                <th className="text-right pr-4">n</th>
                <th className="text-right pr-4">abs 63d</th>
                <th className="text-right pr-4">rel 63d</th>
                <th className="text-right pr-4">abs 252d</th>
                <th className="text-right">rel 252d</th>
              </tr>
            </thead>
            <tbody>
              {zoneRows.map(r => (
                <tr key={r.key} className="border-b border-white/5">
                  <td className="py-1.5 pr-4">{r.zone}</td>
                  <td className="pr-4">
                    <span style={{ color: r.dir === 'up' ? C.rise : C.fall }}>
                      {r.dir === 'up' ? '▲ rising' : '▼ falling'}
                    </span>
                  </td>
                  <td className="text-right pr-4 text-gray-500">{r.n?.toLocaleString()}</td>
                  <td className="text-right pr-4" style={divergingCell(r.abs63)}>{pct(r.abs63)}</td>
                  <td className="text-right pr-4" style={divergingCell(r.rel63)}>{pct(r.rel63)}</td>
                  <td className="text-right pr-4" style={divergingCell(r.abs252)}>{pct(r.abs252)}</td>
                  <td className="text-right" style={divergingCell(r.rel252)}>{pct(r.rel252)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h3 className="text-sm font-medium text-gray-200 mt-4">Fresh level crosses — forward relative returns and hit rate</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm text-gray-300">
            <thead>
              <tr className="text-xs text-gray-400 border-b border-white/10">
                <th className="text-left py-2 pr-4">Cross</th>
                <th className="text-right pr-4">n</th>
                <th className="text-right pr-4">rel 63d</th>
                <th className="text-right pr-4">rel 252d</th>
                <th className="text-right">abs-252 hit rate</th>
              </tr>
            </thead>
            <tbody>
              {crossRows.map(r => (
                <tr key={r.key} className="border-b border-white/5">
                  <td className="py-1.5 pr-4" style={{ color: r.dir === 'up' ? C.rise : C.fall }}>{r.label}</td>
                  <td className="text-right pr-4 text-gray-500">{r.n?.toLocaleString()}</td>
                  <td className="text-right pr-4" style={divergingCell(r.rel63)}>{pct(r.rel63)}</td>
                  <td className="text-right pr-4" style={divergingCell(r.rel252)}>{pct(r.rel252)}</td>
                  <td className="text-right">{pct(r.hit252, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Prose>
          <p>
            <b>Direction dominates level.</b> In every one of the five zones, a rising mean_long beats a falling
            one by ~2–3% relative over the next quarter. A falling state is a persistent forward-negative
            signal (−4% to −7% relative over the next year) regardless of where it happens.
          </p>
          <p>
            <b>The alpha lives in the middle of the ascent.</b> A fresh upward cross of 40 or 50 carries the most
            remaining relative return (+6–8% over 252d with ~72% absolute hit rate); by the time 80 is crossed,
            most of the move is spent. And <b>bottom exits are traps at short horizons</b>: a 20↑ cross shows
            −3.5% relative over the next quarter — deep-junk bounces do not persist in relative terms and only
            break even a year out.
          </p>
          <p>
            This validates the wave-engine design directly: entering on <code>cross mean_long 50 up</code> catches
            the highest-carry state transition, and exiting on <code>50 down</code> steps off a state that is
            reliably forward-negative.
          </p>
        </Prose>
      </Section>

      {/* 5. Rules */}
      <Section n="§5" title="The shape, in six rules">
        <ol className="list-decimal list-inside text-sm text-gray-300 space-y-2">
          <li><b>Slow pendulum.</b> Lives in 30–80, speed-capped at 0.45/day, median drift 0.118/day. Zone changes are multi-month commitments — nothing about this indicator is fast.</li>
          <li><b>Direction beats level.</b> Rising beats falling by ~2–3% relative per quarter in every zone. The falling state is the single most reliable (negative) signal in the study.</li>
          <li><b>Buy the middle of the ascent.</b> Fresh 40↑ / 50↑ crosses carry +6–8% relative over the next year at ~72% hit; by 80↑ the move is mostly spent.</li>
          <li><b>Do not chase bottom exits.</b> 20↑ crosses lose −3.5% relative in the next quarter; junk bounces do not persist.</li>
          <li><b>It is late by design.</b> Price leads by ~1 month (peak corr 0.36 at −21d). Accept the lag tax; the payment is phase persistence (median 60d, p90 235d).</li>
          <li><b>Harvest breadth, not frequency.</b> 74% of stocks never complete a 20→80 round trip — the signal volume for wave strategies comes from ~1,200 names, not from any single stock&apos;s cycle.</li>
        </ol>
      </Section>

      {/* Caveats */}
      <section className="rounded-xl glass shadow-md p-6">
        <h2 className="text-sm font-semibold text-gray-200 mb-2">Caveats & method</h2>
        <ul className="text-xs text-gray-400 space-y-1 list-disc list-inside">
          <li>Universe = symbols with computed indicators (current membership family) → survivorship tilt; absolute forward returns are inflated by the bull sample. The <b>relative</b> columns are the robust ones.</li>
          <li>Forward windows overlap (subsampled every 5 days) — medians are fair, dispersion is understated.</li>
          <li>Single benchmark (Nifty 50); small-cap relative returns carry breadth-beta effects.</li>
          <li>Phases from 21d slope sign with runs &lt;10d collapsed; crosses are exact level crossings (no hysteresis).</li>
          <li>Recompute after each data refresh: <code>python scripts/research_meanlong/meanlong_shape.py</code> (stores to <code>research_results</code>; this page only reads).</li>
        </ul>
      </section>
    </div>
  );
}
