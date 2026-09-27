"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Badge, Card, ErrorBox, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { day, dayTime } from "@/lib/format";

export default function Runs() {
  const q = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-[20px] font-semibold">Runs</h1>
      <Card>
        <table className="grid">
          <thead><tr><th>Run</th><th>Model v</th><th>Week</th><th>Signal</th><th>Fill</th><th>Rebalance</th><th>Status</th><th>Checks</th><th>Computed</th><th>Published</th></tr></thead>
          <tbody>
            {q.data!.map((r) => (
              <tr key={r._id}>
                <td><Link className="text-accent hover:underline" href={`/runs/${r._id}`}>{r._id}</Link></td>
                <td className="num">{r.version}</td>
                <td>{r.iso_week}</td>
                <td>{day(r.signal_date)}</td>
                <td>{day(r.fill_date)}</td>
                <td>{r.monthly_reset ? "yes" : ""}</td>
                <td><Badge value={r.status} /></td>
                <td><Badge value={r.verdict} /></td>
                <td className="text-muted">{dayTime(r.created_at)}</td>
                <td className="text-muted">{dayTime(r.published_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
