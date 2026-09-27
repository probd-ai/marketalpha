"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Card, ErrorBox, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { dayTime } from "@/lib/format";

export default function Journal() {
  const q = useQuery({ queryKey: ["journal"], queryFn: api.journal });
  const [filter, setFilter] = useState("");
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const events = [...new Set(q.data!.map((e) => e.event))].sort();
  const rows = q.data!.filter((e) => !filter || e.event === filter);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-[20px] font-semibold">Journal</h1>
          <div className="text-muted">Append-only audit log of every run, publish, execution record and settings change.</div>
        </div>
        <select value={filter} onChange={(e) => setFilter(e.target.value)} className="rounded border border-line bg-panel px-2 py-1">
          <option value="">All events</option>
          {events.map((e) => <option key={e} value={e}>{e}</option>)}
        </select>
      </div>
      <Card>
        <table className="grid">
          <thead><tr><th>When</th><th>Event</th><th>Detail</th></tr></thead>
          <tbody>
            {rows.map((e, i) => (
              <tr key={`${e.at}-${i}`}>
                <td className="whitespace-nowrap text-muted">{dayTime(e.at)}</td>
                <td className="font-medium">{e.event}</td>
                <td className="font-mono text-[12px] text-ink-2">{JSON.stringify(e.detail)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
