"use client";

import { useMemo } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer,
  Tooltip, XAxis, YAxis,
} from "recharts";

import { day, pct } from "@/lib/format";
import { pts } from "@/lib/perf";

const TIP = { background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6, fontSize: 12 };
const AXIS = { stroke: "var(--muted)", fontSize: 11, tickLine: false } as const;

/** One colour per sleeve id, stable across every chart on the page. */
export const SLEEVE_COLOR: Record<string, string> = {
  value: "var(--accent)", growth: "var(--good)", satellite: "#e07b39", gold: "#c9a227", silver: "#9aa3ad", ndx: "#8b6fe8",
};
const FALLBACK = ["#4c9f9f", "#b5568f", "#6b8e23"];
export const sleeveColor = (id: string, i = 0) => SLEEVE_COLOR[id] ?? FALLBACK[i % FALLBACK.length];

export interface LineSpec { key: string; label: string; color: string; dash?: string; width?: number }

/** Several series on one date axis (values already rebased by the backend). */
export function MultiLine({ dates, series, lines, unit = "", height = 280 }: {
  dates: string[]; series: Record<string, (number | null)[] | undefined>; lines: LineSpec[]; unit?: "" | "%"; height?: number;
}) {
  const data = useMemo(() => dates.map((d, i) => {
    const row: Record<string, string | number | null> = { d };
    for (const l of lines) row[l.key] = series[l.key]?.[i] ?? null;
    return row;
  }), [dates, series, lines]);
  const fmt = (v: number) => (unit === "%" ? pct(v, 1) : v.toFixed(0));
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="d" tickFormatter={(d: string) => d.slice(0, 7)} minTickGap={60} {...AXIS} />
          <YAxis domain={["auto", "auto"]} tickFormatter={fmt} width={52} axisLine={false} {...AXIS} />
          <Tooltip contentStyle={TIP} labelFormatter={(d: string) => day(d)}
            formatter={(v: number, k: string) => [unit === "%" ? pct(v, 2) : v.toFixed(2), lines.find((l) => l.key === k)?.label ?? k]} />
          <Legend formatter={(k: string) => lines.find((l) => l.key === k)?.label ?? k} wrapperStyle={{ fontSize: 12 }} />
          {unit === "%" && <ReferenceLine y={0} stroke="var(--line)" />}
          {lines.map((l) => (
            <Line key={l.key} type="monotone" dataKey={l.key} stroke={l.color} strokeWidth={l.width ?? 1.5}
              strokeDasharray={l.dash} dot={false} isAnimationActive={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Underwater chart: the main series shaded, comparisons as lines. Values are drawdowns in percent (≤ 0). */
export function Drawdown({ dates, main, mainLabel, others = [], height = 200 }: {
  dates: string[]; main: (number | null)[]; mainLabel: string;
  others?: { key: string; label: string; values?: (number | null)[]; color: string }[]; height?: number;
}) {
  const data = useMemo(() => dates.map((d, i) => {
    const row: Record<string, string | number | null> = { d, main: main[i] ?? null };
    for (const o of others) row[o.key] = o.values?.[i] ?? null;
    return row;
  }), [dates, main, others]);
  const label = (k: string) => (k === "main" ? mainLabel : others.find((o) => o.key === k)?.label ?? k);
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="d" tickFormatter={(d: string) => d.slice(0, 7)} minTickGap={60} {...AXIS} />
          <YAxis tickFormatter={(v: number) => pct(v, 0)} width={52} axisLine={false} domain={["dataMin", 0]} {...AXIS} />
          <Tooltip contentStyle={TIP} labelFormatter={(d: string) => day(d)} formatter={(v: number, k: string) => [pct(v, 2), label(k)]} />
          <Legend formatter={label} wrapperStyle={{ fontSize: 12 }} />
          <Area type="monotone" dataKey="main" stroke="var(--bad)" fill="var(--bad-soft)" strokeWidth={1.5} isAnimationActive={false} />
          {others.map((o) => (
            <Area key={o.key} type="monotone" dataKey={o.key} stroke={o.color} fill="none" strokeDasharray="4 3"
              strokeWidth={1.2} isAnimationActive={false} />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Sleeve weights over time as a 100% stack, with the fund's rebalance dates marked. */
export function Weights({ dates, weights, labels, resets, height = 240 }: {
  dates: string[]; weights: Record<string, (number | null)[]>; labels: Record<string, string>; resets: string[]; height?: number;
}) {
  const ids = Object.keys(weights);
  const data = useMemo(() => dates.map((d, i) => {
    const row: Record<string, string | number | null> = { d };
    for (const k of ids) row[k] = weights[k][i];
    return row;
  }), [dates, weights, ids]);
  return (
    <div style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="d" tickFormatter={(d: string) => d.slice(0, 7)} minTickGap={60} {...AXIS} />
          <YAxis domain={[0, 100]} tickFormatter={(v: number) => `${v}%`} width={44} axisLine={false} {...AXIS} />
          <Tooltip contentStyle={TIP} labelFormatter={(d: string) => day(d)} formatter={(v: number, k: string) => [pct(v, 1), labels[k] ?? k]} />
          <Legend formatter={(k: string) => labels[k] ?? k} wrapperStyle={{ fontSize: 12 }} />
          {ids.map((k, i) => (
            <Area key={k} type="monotone" dataKey={k} stackId="w" stroke={sleeveColor(k, i)} fill={sleeveColor(k, i)}
              fillOpacity={0.55} strokeWidth={0.8} isAnimationActive={false} />
          ))}
          {resets.map((r) => <ReferenceLine key={r} x={r} stroke="var(--ink-2)" strokeDasharray="2 3"
            label={{ value: "rebalance", position: "insideTop", fontSize: 10, fill: "var(--muted)" }} />)}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Horizontal bars of signed points (contribution, P&L by stock); `avg` draws a dashed marker at that value. */
export function PointsBars({ rows, height, avg }: {
  rows: { name: string; pts: number; color?: string }[]; height?: number; avg?: { value: number; label: string };
}) {
  const h = height ?? Math.max(120, rows.length * 30 + 20) + (avg ? 16 : 0);
  return (
    <div style={{ height: h }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} layout="vertical" margin={{ top: avg ? 16 : 0, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--line)" horizontal={false} />
          {/* zero always on the axis: bars grow from 0, so an axis that starts at −0.3 misplaces every bar and marker */}
          <XAxis type="number" tickFormatter={(v: number) => v.toFixed(1)} {...AXIS}
            domain={[(lo: number) => Math.min(0, lo), (hi: number) => Math.max(0, hi)]} />
          <YAxis type="category" dataKey="name" width={110} axisLine={false} {...AXIS} />
          <ReferenceLine x={0} stroke="var(--muted)" />
          <Tooltip contentStyle={TIP} cursor={{ fill: "var(--panel-2)" }} formatter={(v: number) => [pts(v), "contribution"]} />
          <Bar dataKey="pts" isAnimationActive={false} radius={2}>
            {rows.map((r) => <Cell key={r.name} fill={r.color ?? (r.pts >= 0 ? "var(--good)" : "var(--bad)")} />)}
          </Bar>
          {/* after the bars, so the marker is painted on top of them */}
          {avg && (
            <ReferenceLine x={avg.value} stroke="var(--ink)" strokeDasharray="5 4" strokeWidth={1.5} ifOverflow="extendDomain"
              label={{ value: avg.label, position: "top", fontSize: 11, fill: "var(--ink-2)" }} />
          )}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
