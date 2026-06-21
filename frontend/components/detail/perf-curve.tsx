"use client";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { CurvePoint } from "@/lib/types";
import { formatLakh } from "@/lib/format";

export function PerfCurve({ curve }: { curve: CurvePoint[] }) {
  return (
    <div data-testid="perf-curve" style={{ width: "100%", height: 240 }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={curve} margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="pf" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="#34d399" stopOpacity={0.34} />
              <stop offset="1" stopColor="#34d399" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="rgba(255,255,255,.06)" vertical={false} />
          <XAxis dataKey="date" tick={{ fill: "#93a0bd", fontSize: 10 }} minTickGap={40} stroke="rgba(255,255,255,.1)" />
          <YAxis tickFormatter={(v) => formatLakh(v)} tick={{ fill: "#93a0bd", fontSize: 10 }}
            width={60} stroke="rgba(255,255,255,.1)" domain={["auto", "auto"]} />
          <Tooltip formatter={(v: number) => formatLakh(v)}
            contentStyle={{ background: "#11162a", border: "1px solid rgba(255,255,255,.12)", borderRadius: 8, fontSize: 12 }} />
          <Area type="monotone" dataKey="benchmark_value" name="Nifty 50" stroke="#7c8bb0" strokeWidth={1.6} fill="none" />
          <Area type="monotone" dataKey="portfolio_value" name="Portfolio" stroke="#34d399" strokeWidth={2} fill="url(#pf)" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
