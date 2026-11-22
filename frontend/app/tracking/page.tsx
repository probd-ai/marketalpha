"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { ManualTrades } from "@/components/ManualTrades";
import { SortTable } from "@/components/SortTable";
import { Card, Empty, ErrorBox, Kpi, Loading, Signed } from "@/components/ui";
import { api, type ActualRow, type SleeveTracking, type TaggedRow } from "@/lib/api";
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
        <div className="text-muted">Actual = your recorded executions on published tickets plus manual trades. Model = latest run&apos;s books in whole shares. Valued at the latest close.</div>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
        <Kpi label="Positions with a gap" value={`${gaps.length} / ${d.rows.length}`} tone={gaps.length ? "bad" : "good"} />
        <Kpi label="Capital not deployed" value={rupees(d.undeployed)} tone={d.undeployed > 0 ? "bad" : "good"}
          sub={d.tagged_value > 0 ? `after ${rupees(d.tagged_value)} of other holdings tagged to sleeves — the next ticket won't buy the rest on its own`
            : "model holdings you don't own — the next ticket won't buy them on its own"} />
        <Kpi label="Tracking difference" value={rupees(d.tracking_value)} sub="sum of |gap| × price" />
        <Kpi label="Other holdings" value={rupees(d.other_value)} sub={<Link href="/holdings" className="text-accent hover:underline">untagged · outside the books — tag one to a sleeve to count it →</Link>} />
      </div>
      {d.sleeves.length > 0 && (
        <Card title="By sleeve">
          <SortTable<SleeveTracking> rows={d.sleeves} rowKey={(s) => s.sleeve} initialSort={{ key: "to_deploy", dir: -1 }}
            columns={[
              { key: "sleeve", label: "Sleeve", render: (s) => <span className="font-semibold">{s.sleeve}</span> },
              { key: "model_value", label: "Model ₹", align: "right", render: (s) => rupees(s.model_value) },
              { key: "actual_value", label: "Actual ₹", align: "right", render: (s) => rupees(s.actual_value) },
              { key: "under_value", label: "Under-invested ₹", align: "right", render: (s) => <Signed v={-s.under_value}>{rupees(s.under_value)}</Signed> },
              { key: "over_value", label: "Over-invested ₹", align: "right", render: (s) => rupees(s.over_value) },
              { key: "tagged_value", label: "Tagged other ₹", align: "right", render: (s) => (s.tagged_value ? rupees(s.tagged_value) : "—") },
              { key: "to_deploy", label: "Still to deploy ₹", align: "right", render: (s) => <Signed v={-s.to_deploy}>{rupees(s.to_deploy)}</Signed> },
              { key: "unpriced", label: "Unpriced", align: "right", render: (s) => (s.unpriced ? s.unpriced : "—") },
            ]} />
        </Card>
      )}
      {d.rows.length ? (
        <Card title="Positions">
          <SortTable<ActualRow> rows={d.rows} rowKey={(r) => `${r.sleeve}|${r.symbol}`} initialSort={{ key: "gap_value", dir: -1 }}
            columns={[
              { key: "sleeve", label: "Sleeve" },
              { key: "symbol", label: "Symbol", render: (r) => <span className="font-semibold">{r.symbol}</span> },
              { key: "model_qty", label: "Model", align: "right", render: (r) => qty(r.model_qty) },
              { key: "actual_qty", label: "Actual", align: "right", render: (r) => (
                <>{qty(r.actual_qty)}{r.tagged_qty > 0 && <span className="ml-1 text-[11.5px] text-muted" title="from other holdings tagged to this sleeve">({qty(r.tagged_qty)} tagged)</span>}</>
              ) },
              { key: "gap_qty", label: "Gap", align: "right", render: (r) => <Signed v={r.gap_qty}>{qty(r.gap_qty)}</Signed> },
              { key: "gap_value", label: "Gap ₹", align: "right", value: (r) => Math.abs(r.gap_value ?? 0), render: (r) => rupees(r.gap_value) },
            ]} />
        </Card>
      ) : <Empty>Nothing to compare yet — publish a ticket and record executions.</Empty>}
      {d.tagged.length > 0 && (
        <Card title="Tagged other holdings — held for a sleeve, not in its model">
          <div className="mb-2 text-[12.5px] text-muted">Counted by value toward the sleeve&apos;s capital (tracking only — fund NAV is the model). Tag or untag on <Link href="/holdings" className="text-accent hover:underline">Other holdings</Link>.</div>
          <SortTable<TaggedRow> rows={d.tagged} rowKey={(t) => `${t.sleeve}|${t.symbol}`} initialSort={{ key: "value", dir: -1 }}
            columns={[
              { key: "sleeve", label: "Sleeve" },
              { key: "symbol", label: "Symbol", render: (t) => <span className="font-semibold">{t.symbol}</span> },
              { key: "qty", label: "Shares", align: "right", render: (t) => qty(t.qty) },
              { key: "value", label: "Value ₹", align: "right", value: (t) => t.value ?? 0, render: (t) => rupees(t.value) },
            ]} />
        </Card>
      )}
      <ManualTrades />
    </div>
  );
}
