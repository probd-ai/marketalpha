"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, type Job } from "@/lib/api";

import { Button, ErrorBox } from "./ui";

/** Starts a weekly run (async job on the backend) and follows it to the run review page. */
export function RunButton({ runningJob }: { runningJob?: Job | null }) {
  const [jobId, setJobId] = useState<string | null>(runningJob?.job_id ?? null);
  const [msg, setMsg] = useState<string>(runningJob?.message ?? "");
  const [err, setErr] = useState<unknown>(null);
  const router = useRouter();
  const qc = useQueryClient();

  useEffect(() => {
    if (!jobId) return;
    let alive = true;
    const tick = async () => {
      try {
        const j = await api.job(jobId);
        if (!alive) return;
        setMsg(j.message);
        if (j.status === "running") { setTimeout(tick, 2000); return; }
        setJobId(null);
        qc.invalidateQueries();
        if (j.status === "done" && j.result) router.push(`/runs/${j.result}`);
        else setErr(new Error(j.error ?? "run failed"));
      } catch (e) { if (alive) { setErr(e); setJobId(null); } }
    };
    tick();
    return () => { alive = false; };
  }, [jobId, qc, router]);

  return (
    <div className="flex flex-col items-end gap-2">
      <Button kind="primary" disabled={!!jobId} onClick={async () => {
        setErr(null);
        try { setJobId((await api.startRun()).job_id); setMsg("starting"); } catch (e) { setErr(e); }
      }}>
        {jobId ? `Computing… ${msg}` : "Compute this week's run"}
      </Button>
      {jobId && <div className="text-[12px] text-muted">Loads prices and indicators for all five sleeves — about a minute.</div>}
      <ErrorBox error={err} />
    </div>
  );
}
