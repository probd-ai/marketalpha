"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { EquityChart } from "@/components/EquityChart";
import { RunButton } from "@/components/RunButton";
import { Badge, Card, Empty, ErrorBox, Kpi, Loading, Signed } from "@/components/ui";
import { api } from "@/lib/api";
import { day, dayTime, pct, rupees } from "@/lib/format";

export default function Desk() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 30_000 });
  const books = useQuery({ queryKey: ["books"], queryFn: api.books });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  if (status.isLoading) return <Loading />;
  if (status.error) return <ErrorBox error={status.error} />;
  const s = status.data!;
  const f = books.data?.fund;
  const stale = s.latest_prices && s.last_run && s.last_run.signal_date < s.latest_prices;
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-[20px] font-semibold">{s.fund_name}</h1>
          <div className="text-muted">
            Capital {rupees(s.capital)} · model from {day(s.model_start)} · model version {s.config_version} ·
            prices to {day(s.latest_prices)}
          </div>
        </div>
        <RunButton runningJob={s.job} />
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <Card title="This week">
          {s.open_tickets.length > 0 ? (
            <div>
              <div className="mb-1"><Badge value="EXECUTING" label="Ticket open" /></div>
              {s.open_tickets.map((id) => (
                <Link key={id} className="block text-accent hover:underline" href={`/runs/${id}/ticket`}>Record executions for {id} →</Link>
              ))}
            </div>
          ) : s.last_run?.status === "DRAFT" ? (
            <div>
              <div className="mb-1"><Badge value="DRAFT" label="Draft waiting" /></div>
              <Link className="text-accent hover:underline" href={`/runs/${s.last_run._id}`}>Review run for {day(s.last_run.fill_date)} →</Link>
            </div>
          ) : (
            <div className="text-ink-2">No open ticket. Compute a run after the market closes on your trading day.</div>
          )}
        </Card>
        <Card title="Last run">
          {s.last_run ? (
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2"><Badge value={s.last_run.status} /><Badge value={s.last_run.verdict} label={`checks ${s.last_run.verdict}`} /></div>
              <div>Signal {day(s.last_run.signal_date)} → fill {day(s.last_run.fill_date)}</div>
              <div className="text-muted">computed {dayTime(s.last_run.created_at)}</div>
              {stale && <div className="text-warn">Newer prices exist — compute a fresh run before trading.</div>}
            </div>
          ) : <div className="text-ink-2">No run yet.</div>}
        </Card>
        <Card title="Last published">
          {s.last_published ? (
            <div className="flex flex-col gap-1">
              <div><Link className="text-accent hover:underline" href={`/runs/${s.last_published._id}`}>{s.last_published._id}</Link></div>
              <div>{s.last_published.iso_week} · fill {day(s.last_published.fill_date)}</div>
              <Badge value={s.last_published.status} />
            </div>
          ) : <div className="text-ink-2">Nothing published under this model version.</div>}
        </Card>
      </div>

      {f ? (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
            <Kpi label="Fund value (model)" value={rupees(f.value)}
              sub={f.live_since ? `net invested ${rupees(f.net_invested ?? f.capital)} · live since ${day(f.live_since)}` : "not live — equals capital until the first ticket is published"} />
            <Kpi label="Yearly return (back-test)" value={pct(f.cagr)} sub={`since ${day(f.model_start)} · Nifty 50 ${pct(f.nifty.cagr)}`} />
            <Kpi label="Worst fall" value={pct(f.max_dd)} sub={`now ${pct(f.dd_now)} from peak`} tone="bad" />
            <Kpi label="1 month" value={<Signed v={f.r_1m}>{pct(f.r_1m, 1, true)}</Signed>} sub={`Nifty ${pct(f.nifty.r_1m, 1, true)}`} />
            <Kpi label="3 months" value={<Signed v={f.r_3m}>{pct(f.r_3m, 1, true)}</Signed>} sub={`Nifty ${pct(f.nifty.r_3m, 1, true)}`} />
            <Kpi label="12 months" value={<Signed v={f.r_1y}>{pct(f.r_1y, 1, true)}</Signed>} sub={`Nifty ${pct(f.nifty.r_1y, 1, true)}`} />
          </div>
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-3">
            <Card title="Model back-test vs Nifty 50" className="xl:col-span-2">
              {books.data?.curve && <EquityChart curve={books.data.curve} />}
              <div className="mt-2 text-[12px] text-muted">Back-test of the model with the fund&apos;s capital invested at the model start (point-in-time, next-open fills, full costs) — simulated, not realised returns.</div>
            </Card>
            <Card title="Sleeves">
              <table className="grid">
                <thead><tr><th>Sleeve</th><th className="r">Target</th><th className="r">Now</th><th className="r">Value</th><th className="r">%/yr</th></tr></thead>
                <tbody>
                  {books.data!.sleeves!.map((sl) => (
                    <tr key={sl.id}>
                      <td>{sl.label}</td>
                      <td className="r num">{pct(sl.target_weight, 0)}</td>
                      <td className="r num">{pct(sl.weight, 1)}</td>
                      <td className="r num">{rupees(sl.value)}</td>
                      <td className="r num">{pct(sl.cagr)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-2 text-[12px] text-muted">Weights drift between fund rebalances (scheduled months, or early when a sleeve drifts past the band — see Settings).</div>
            </Card>
          </div>
        </>
      ) : books.isLoading ? <Loading what="Loading model" /> : <Empty>No model yet — compute the first run.</Empty>}

      <Card title="Recent runs" right={<Link href="/runs" className="text-[12px] text-accent hover:underline">All runs →</Link>}>
        {runs.data?.length ? (
          <table className="grid">
            <thead><tr><th>Run</th><th>Week</th><th>Signal → fill</th><th>Status</th><th>Checks</th><th>Computed</th></tr></thead>
            <tbody>
              {runs.data.slice(0, 6).map((r) => (
                <tr key={r._id}>
                  <td><Link className="text-accent hover:underline" href={`/runs/${r._id}`}>{r._id}</Link></td>
                  <td>{r.iso_week}</td>
                  <td>{day(r.signal_date)} → {day(r.fill_date)}</td>
                  <td><Badge value={r.status} /></td>
                  <td><Badge value={r.verdict} /></td>
                  <td className="text-muted">{dayTime(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <div className="text-muted">No runs yet.</div>}
      </Card>
    </div>
  );
}
