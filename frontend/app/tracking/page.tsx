"use client";

import { useQuery } from "@tanstack/react-query";

import { SortTable } from "@/components/SortTable";
import { Card, Empty, ErrorBox, Kpi, Loading, Signed } from "@/components/ui";
import { api, type ActualRow } from "@/lib/api";
import { qty, rupees } from "@/lib/format";

export default function Tracking() {
  const q = useQuery({ queryKey: ["actual"], queryFn: api.actual });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const d = q.data!;
  const gaps = d.rows.filter((r) => r.gap_qty !== 0);
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-[20px] font-semibold">Tracking: model vs actual</h1>
        <div className="text-muted">Actual = sum of your recorded executions on published tickets. Model = latest run&apos;s books in whole shares.</div>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Kpi label="Positions compared" value={d.rows.length} />
        <Kpi label="Positions with a gap" value={gaps.length} tone={gaps.length ? "bad" : "good"} />
        <Kpi label="Tracking difference" value={rupees(d.tracking_value)} sub="sum of |gap| × price" />
      </div>
      {d.rows.length ? (
        <Card title="Positions">
          <SortTable<ActualRow> rows={d.rows} rowKey={(r) => `${r.sleeve}|${r.symbol}`} initialSort={{ key: "gap_value", dir: -1 }}
            columns={[
              { key: "sleeve", label: "Sleeve" },
              { key: "symbol", label: "Symbol", render: (r) => <span className="font-semibold">{r.symbol}</span> },
              { key: "model_qty", label: "Model", align: "right", render: (r) => qty(r.model_qty) },
              { key: "actual_qty", label: "Actual", align: "right", render: (r) => qty(r.actual_qty) },
              { key: "gap_qty", label: "Gap", align: "right", render: (r) => <Signed v={r.gap_qty}>{qty(r.gap_qty)}</Signed> },
              { key: "gap_value", label: "Gap ₹", align: "right", value: (r) => Math.abs(r.gap_value ?? 0), render: (r) => rupees(r.gap_value) },
            ]} />
        </Card>
      ) : <Empty>Nothing to compare yet — publish a ticket and record executions.</Empty>}
    </div>
  );
}
