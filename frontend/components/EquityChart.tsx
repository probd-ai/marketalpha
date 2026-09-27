"use client";

import { useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { Curve } from "@/lib/api";
import { day, rupees } from "@/lib/format";

const RANGES = { "1Y": 365, "3Y": 365 * 3, ALL: 0 } as const;

/** Fund value in ₹ vs Nifty 50 rebased to the same starting capital. Log scale, one axis. */
export function EquityChart({ curve }: { curve: Curve }) {
  const [range, setRange] = useState<keyof typeof RANGES>("ALL");
  const data = useMemo(() => {
    const rows = curve.dates.map((d, i) => ({ d, fund: curve.fund[i], nifty: curve.nifty[i] }));
    const days = RANGES[range];
    if (!days) return rows;
    const cut = new Date(new Date(rows[rows.length - 1].d).getTime() - days * 864e5).toISOString().slice(0, 10);
    const sub = rows.filter((r) => r.d >= cut);
    const f0 = sub[0].fund, n0 = sub[0].nifty;
    return sub.map((r) => ({ ...r, nifty: (r.nifty / n0) * f0 }));      // rebase Nifty to the fund at the window start
  }, [curve, range]);
  return (
    <div>
      <div className="mb-2 flex justify-end gap-1 no-print">
        {(Object.keys(RANGES) as (keyof typeof RANGES)[]).map((k) => (
          <button key={k} onClick={() => setRange(k)}
            className={`rounded px-2 py-0.5 text-[12px] ${range === k ? "bg-accent-soft text-accent font-semibold" : "text-muted hover:text-ink"}`}>{k}</button>
        ))}
      </div>
      <div className="h-[300px]">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 12, bottom: 0, left: 8 }}>
            <CartesianGrid stroke="var(--line)" vertical={false} />
            <XAxis dataKey="d" tickFormatter={(d: string) => d.slice(0, 7)} minTickGap={60} stroke="var(--muted)" fontSize={11} tickLine={false} />
            <YAxis scale="log" domain={["auto", "auto"]} tickFormatter={(v: number) => rupees(v)} stroke="var(--muted)" fontSize={11}
              tickLine={false} axisLine={false} width={70} />
            <Tooltip formatter={(v: number, n: string) => [rupees(v), n === "fund" ? "Fund" : "Nifty 50"]} labelFormatter={(d: string) => day(d)}
              contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6, fontSize: 12 }} />
            <Legend formatter={(v: string) => (v === "fund" ? "Fund (model)" : "Nifty 50, same start")} wrapperStyle={{ fontSize: 12 }} />
            <Line type="monotone" dataKey="fund" stroke="var(--accent)" strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line type="monotone" dataKey="nifty" stroke="var(--muted)" strokeWidth={1.5} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
