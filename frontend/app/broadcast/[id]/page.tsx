"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";

import { PaperView } from "@/components/PaperView";
import { Badge, Button, Card, ErrorBox, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { dayTime } from "@/lib/format";

export default function PaperPage() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["paper", id], queryFn: () => api.paper(id) });
  const archive = useMutation({ mutationFn: () => api.archivePaper(id), onSuccess: () => qc.invalidateQueries() });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const p = q.data!;
  return (
    <div className="flex max-w-5xl flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge value={p.kind} label={p.kind.replace("-", " ")} />
            {p.status === "ARCHIVED" && <Badge value="CLOSED" label="archived" />}
            <span className="text-[12px] text-muted">{dayTime(p.created_at)} · {p.author}</span>
            {p.run_id && <Link href={`/runs/${p.run_id}/ticket`} className="text-[12px] text-accent hover:underline">ticket {p.run_id} →</Link>}
          </div>
          <h1 className="mt-1 text-[22px] font-semibold">{p.title}</h1>
          {p.summary && <div className="mt-1 max-w-3xl text-[14px] text-ink-2">{p.summary}</div>}
        </div>
        <div className="flex gap-2 no-print">
          <Link href="/broadcast"><Button>All papers</Button></Link>
          <Button onClick={() => window.print()}>Print</Button>
          {p.status === "PUBLISHED" && <Button disabled={archive.isPending} onClick={() => archive.mutate()}>Archive</Button>}
        </div>
      </div>
      <ErrorBox error={archive.error} />
      <Card><PaperView sections={p.sections ?? []} /></Card>
    </div>
  );
}
