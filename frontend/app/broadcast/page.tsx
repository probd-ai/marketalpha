"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Badge, Card, Empty, ErrorBox, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { dayTime } from "@/lib/format";

/** AI Broadcast: papers the AI publishes to the desk — trade plans, rebalance briefs, research notes. */
export default function Broadcast() {
  const q = useQuery({ queryKey: ["papers"], queryFn: api.papers });
  const [showArchived, setShowArchived] = useState(false);
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const papers = q.data!.filter((p) => showArchived || p.status === "PUBLISHED");
  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold">AI Broadcast</h1>
          <div className="text-muted">Papers published by Claude — trade plans, rebalance briefs and research notes. Read, print, archive.</div>
        </div>
        <label className="flex items-center gap-2 text-[13px] text-ink-2">
          <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} /> show archived
        </label>
      </div>
      {papers.length ? papers.map((p) => (
        <Link key={p._id} href={`/broadcast/${p._id}`} className="block">
          <Card className="hover:border-accent">
            <div className="flex flex-wrap items-center gap-2">
              <Badge value={p.kind} label={p.kind.replace("-", " ")} />
              {p.status === "ARCHIVED" && <Badge value="CLOSED" label="archived" />}
              <span className="text-[12px] text-muted">{dayTime(p.created_at)} · {p.author}{p.run_id ? ` · run ${p.run_id}` : ""}</span>
            </div>
            <div className="mt-1 text-[16px] font-semibold">{p.title}</div>
            {p.summary && <div className="mt-1 text-[13px] text-ink-2">{p.summary}</div>}
          </Card>
        </Link>
      )) : <Empty>No papers yet.</Empty>}
    </div>
  );
}
