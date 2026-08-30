'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Loader2, Link2, Trash2, RefreshCcw, Plus, AlertTriangle } from 'lucide-react';
import { inr, Portfolio } from '@/lib/portfolioApi';

const BASE = '/api';

function apiKeyHeaders(): Record<string, string> {
  const k = typeof window !== 'undefined' ? window.localStorage.getItem('mh_api_key') : null;
  return k ? { 'X-API-Key': k } : {};
}

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const r = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...apiKeyHeaders(), ...(init.headers || {}) },
  });
  if (!r.ok) {
    const b = await r.json().catch(() => null);
    throw new Error(b && typeof b.detail === 'string' ? b.detail : `HTTP ${r.status}`);
  }
  return r.json();
}

interface MappingSettings {
  capital_mode: 'account' | 'sim' | 'fixed';
  fixed_capital: number | null;
  tolerance_percent: number;
  min_order_value: number;
  sell_unknown: boolean;
  product: string;
  order_type: string;
  limit_band_percent?: number;
  max_participation_percent?: number;
  exclude_symbols?: string[];
}

interface Mapping {
  id: string;
  portfolio_id: string;
  portfolio_name?: string;
  broker: string;
  account?: string;
  label: string;
  settings: MappingSettings;
}

interface ActionRow {
  punched_qty?: number;
  filled_qty?: number;
  order_ids?: string[];
  limit_price?: number | null;
  participation_percent?: number | null;
  liquidity_capped?: boolean;
  suggested_slices?: number;
  seq: number;
  action: 'BUY' | 'SELL';
  symbol: string;
  product: string;
  order_type: string;
  sim_qty: number;
  live_qty: number;
  qty: number;
  last_price: number;
  est_value: number;
  target_value: number;
  live_value: number;
  reason: string;
  drift_percent: number | null;
  status: string;
}

interface ExecOrder {
  seq: number; symbol: string; side: string; qty: number; price: number;
  est_value: number; order_id?: string | null; broker_status?: string; error?: string | null;
}

interface SyncPlan {
  plan_id: string;
  mapping_id: string;
  broker: string;
  account?: string;
  feasible?: boolean;
  approved_at?: string;
  generated_at: string;
  sim_as_of: string;
  status: string;
  totals: {
    sim_value: number; live_value: number; target_total: number; scale_factor: number;
    available_cash: number; buy_value: number; sell_value: number; net_cash_needed: number;
    action_count: number;
  };
  actions: ActionRow[];
  skipped: { symbol: string; reason: string }[];
  warnings: string[];
}

const inputCls = 'p-1.5 border border-gray-300 rounded-md text-sm bg-white/70';

export default function SyncPanel({ portfolios }: { portfolios: Portfolio[] }) {
  const [mappings, setMappings] = useState<Mapping[]>([]);
  const [plan, setPlan] = useState<SyncPlan | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState({
    portfolio_id: '', broker: 'dhan', account: 'default', label: '',
    capital_mode: 'account' as MappingSettings['capital_mode'],
    fixed_capital: 100000, tolerance_percent: 5, min_order_value: 500, sell_unknown: false,
    max_participation_percent: 10, limit_band_percent: 1, exclude_symbols: '',
  });

  const load = useCallback(() => {
    req<{ mappings: Mapping[] }>('/broker-mapping')
      .then((d) => setMappings(d.mappings))
      .catch((e) => setError(e.message));
  }, []);

  useEffect(load, [load]);

  const createMapping = async () => {
    if (!form.portfolio_id) return setError('Pick a portfolio.');
    setBusy('create');
    setError(null);
    try {
      await req('/broker-mapping', {
        method: 'POST',
        body: JSON.stringify({
          portfolio_id: form.portfolio_id, broker: form.broker, account: form.account || 'default',
          label: form.label || undefined,
          settings: {
            capital_mode: form.capital_mode,
            fixed_capital: form.capital_mode === 'fixed' ? form.fixed_capital : null,
            tolerance_percent: form.tolerance_percent,
            min_order_value: form.min_order_value,
            sell_unknown: form.sell_unknown,
            max_participation_percent: form.max_participation_percent,
            limit_band_percent: form.limit_band_percent,
            exclude_symbols: form.exclude_symbols.split(',').map((s) => s.trim()).filter(Boolean),
          },
        }),
      });
      setShowForm(false);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'create failed');
    } finally {
      setBusy(null);
    }
  };

  const removeMapping = async (id: string) => {
    if (!window.confirm('Delete this mapping (and stop generating plans for it)?')) return;
    try {
      await req(`/broker-mapping/${id}`, { method: 'DELETE' });
      if (plan?.mapping_id === id) setPlan(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'delete failed');
    }
  };

  const generatePlan = async (id: string) => {
    setBusy(id);
    setError(null);
    try {
      const d = await req<{ plan: SyncPlan }>(`/broker-mapping/${id}/sync-plan`, { method: 'POST' });
      setPlan(d.plan);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'sync failed');
    } finally {
      setBusy(null);
    }
  };

  const [fraction, setFraction] = useState(0.2);
  const [dryRun, setDryRun] = useState(true);
  const [execResult, setExecResult] = useState<{ dry_run: boolean; orders?: ExecOrder[]; results?: ExecOrder[]; note?: string; punched?: number; failed?: number } | null>(null);

  const approvePlan = async () => {
    if (!plan) return;
    setBusy('approve');
    setError(null);
    try {
      await req(`/sync-plan/${plan.plan_id}/approve`, { method: 'POST' });
      const d = await req<{ plan: SyncPlan }>(`/sync-plan/${plan.plan_id}`);
      setPlan(d.plan);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'approve failed');
    } finally {
      setBusy(null);
    }
  };

  const executePlan = async () => {
    if (!plan) return;
    if (!dryRun && !window.confirm(
      `REAL ORDERS: punch ${Math.round(fraction * 100)}% of plan ${plan.plan_id} to ${plan.broker}${plan.account && plan.account !== 'default' ? ':' + plan.account : ''}?`)) return;
    setBusy('execute');
    setError(null);
    setExecResult(null);
    try {
      const d = await req<{ dry_run: boolean; orders?: ExecOrder[]; results?: ExecOrder[]; note?: string; punched?: number; failed?: number }>(
        `/sync-plan/${plan.plan_id}/execute?fraction=${fraction}&dry_run=${dryRun}`, { method: 'POST' });
      setExecResult(d);
      const refreshed = await req<{ plan: SyncPlan }>(`/sync-plan/${plan.plan_id}`);
      setPlan(refreshed.plan);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'execute failed');
    } finally {
      setBusy(null);
    }
  };

  const refreshFills = async () => {
    if (!plan) return;
    setBusy('fills');
    try {
      await req(`/sync-plan/${plan.plan_id}/refresh-status`, { method: 'POST' });
      const d = await req<{ plan: SyncPlan }>(`/sync-plan/${plan.plan_id}`);
      setPlan(d.plan);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'status refresh failed');
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-2 flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base flex items-center gap-2">
          <Link2 className="w-4 h-4 text-indigo-600" /> Account ↔ portfolio sync
          <span className="text-xs font-normal text-gray-500">
            plan table = future execution input · rows are &quot;planned&quot; only, nothing is sent to the broker
          </span>
        </CardTitle>
        <Button size="sm" variant="outline" onClick={() => setShowForm((v) => !v)}>
          <Plus className="w-4 h-4 mr-1" /> Map account
        </Button>
      </CardHeader>
      <CardContent className="space-y-4">
        {error && <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}

        {showForm && (
          <div className="rounded border border-gray-200 p-3 grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <div>
              <label className="block text-[11px] text-gray-500 mb-1">Portfolio</label>
              <select className={inputCls + ' w-full'} value={form.portfolio_id}
                onChange={(e) => setForm({ ...form, portfolio_id: e.target.value })}>
                <option value="">select…</option>
                {portfolios.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[11px] text-gray-500 mb-1">Broker account</label>
              <select className={inputCls + ' w-full'} value={form.broker}
                onChange={(e) => setForm({ ...form, broker: e.target.value })}>
                <option value="dhan">Dhan</option>
                <option value="kite">Zerodha Kite</option>
              </select>
            </div>
            <div>
              <label className="block text-[11px] text-gray-500 mb-1">Account alias</label>
              <input className={inputCls + ' w-full'} value={form.account}
                placeholder="default"
                onChange={(e) => setForm({ ...form, account: e.target.value })} />
            </div>
            <div>
              <label className="block text-[11px] text-gray-500 mb-1">Max % of 20d ADV</label>
              <input type="number" min={0} className={inputCls + ' w-full'} value={form.max_participation_percent}
                onChange={(e) => setForm({ ...form, max_participation_percent: Number(e.target.value) })} />
            </div>
            <div>
              <label className="block text-[11px] text-gray-500 mb-1">Limit band %</label>
              <input type="number" min={0} step={0.1} className={inputCls + ' w-full'} value={form.limit_band_percent}
                onChange={(e) => setForm({ ...form, limit_band_percent: Number(e.target.value) })} />
            </div>
            <div className="col-span-2">
              <label className="block text-[11px] text-gray-500 mb-1">Exclude symbols (comma-separated)</label>
              <input className={inputCls + ' w-full'} value={form.exclude_symbols}
                placeholder="e.g. ACUTAAS, ABSLAMC"
                onChange={(e) => setForm({ ...form, exclude_symbols: e.target.value })} />
            </div>
            <div>
              <label className="block text-[11px] text-gray-500 mb-1">Capital sizing</label>
              <select className={inputCls + ' w-full'} value={form.capital_mode}
                onChange={(e) => setForm({ ...form, capital_mode: e.target.value as MappingSettings['capital_mode'] })}>
                <option value="account">Account size (holdings + cash)</option>
                <option value="fixed">Fixed amount</option>
                <option value="sim">Sim values 1:1</option>
              </select>
            </div>
            {form.capital_mode === 'fixed' && (
              <div>
                <label className="block text-[11px] text-gray-500 mb-1">Fixed capital (₹)</label>
                <input type="number" className={inputCls + ' w-full'} value={form.fixed_capital}
                  onChange={(e) => setForm({ ...form, fixed_capital: Number(e.target.value) })} />
              </div>
            )}
            <div>
              <label className="block text-[11px] text-gray-500 mb-1">Drift tolerance %</label>
              <input type="number" min={0} className={inputCls + ' w-full'} value={form.tolerance_percent}
                onChange={(e) => setForm({ ...form, tolerance_percent: Number(e.target.value) })} />
            </div>
            <div>
              <label className="block text-[11px] text-gray-500 mb-1">Min order value (₹)</label>
              <input type="number" min={0} className={inputCls + ' w-full'} value={form.min_order_value}
                onChange={(e) => setForm({ ...form, min_order_value: Number(e.target.value) })} />
            </div>
            <div className="flex items-end pb-1">
              <label className="flex items-center gap-2 text-[11px] text-gray-700">
                <input type="checkbox" checked={form.sell_unknown}
                  onChange={(e) => setForm({ ...form, sell_unknown: e.target.checked })} />
                Sell holdings not in sim (dedicated account only!)
              </label>
            </div>
            <div className="flex items-end">
              <Button size="sm" onClick={createMapping} disabled={busy === 'create'}>
                {busy === 'create' ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Create mapping'}
              </Button>
            </div>
          </div>
        )}

        {mappings.length === 0 && !showForm && (
          <p className="text-xs text-gray-500">
            No mappings yet. Map a broker account to a portfolio, then generate a sync plan — the table of
            BUY/SELL actions needed to align the real account with the simulation.
          </p>
        )}

        {mappings.map((m) => (
          <div key={m.id} className="flex items-center justify-between rounded border border-gray-200 px-3 py-2 text-xs">
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant="secondary">{m.broker}{m.account && m.account !== 'default' ? ':' + m.account : ''}</Badge>
              <span className="font-medium">{m.portfolio_name}</span>
              <span className="text-gray-400">
                {m.settings.capital_mode === 'fixed'
                  ? `fixed ₹${(m.settings.fixed_capital ?? 0).toLocaleString('en-IN')}`
                  : m.settings.capital_mode}
                {' · '}tol {m.settings.tolerance_percent}% · min ₹{m.settings.min_order_value}
                {m.settings.sell_unknown ? ' · sells unknown' : ''}
              </span>
            </div>
            <div className="flex items-center gap-1">
              <Button size="sm" onClick={() => generatePlan(m.id)} disabled={busy === m.id}>
                {busy === m.id ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : <RefreshCcw className="w-4 h-4 mr-1" />}
                Sync plan
              </Button>
              <Button size="sm" variant="ghost" onClick={() => removeMapping(m.id)}>
                <Trash2 className="w-4 h-4 text-red-500" />
              </Button>
            </div>
          </div>
        ))}

        {plan && (
          <div className="space-y-3">
            <div className="flex items-center gap-2 flex-wrap text-xs">
              <Badge variant="outline">{plan.plan_id}</Badge>
              <Badge className="bg-indigo-100 text-indigo-800">{plan.status}</Badge>
              {plan.feasible === false && <Badge className="bg-red-100 text-red-800">NOT FUNDED</Badge>}
              {plan.feasible === true && <Badge className="bg-emerald-100 text-emerald-800">funded</Badge>}
              <span className="text-gray-500">
                generated {plan.generated_at?.slice(0, 16).replace('T', ' ')} · sim as of{' '}
                {plan.sim_as_of?.slice(0, 16).replace('T', ' ')} · scale ×{plan.totals.scale_factor}
              </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-2 text-xs">
              {(
                [
                  ['Target total', plan.totals.target_total],
                  ['Live value', plan.totals.live_value],
                  ['Cash', plan.totals.available_cash],
                  ['To buy', plan.totals.buy_value],
                  ['To sell', plan.totals.sell_value],
                  ['Net cash needed', plan.totals.net_cash_needed],
                ] as [string, number][]
              ).map(([label, v]) => (
                <div key={label} className="rounded border border-gray-200 px-2 py-1.5">
                  <div className="text-[10px] uppercase text-gray-500">{label}</div>
                  <div className="font-semibold">{inr(v)}</div>
                </div>
              ))}
              <div className="rounded border border-gray-200 px-2 py-1.5">
                <div className="text-[10px] uppercase text-gray-500">Actions</div>
                <div className="font-semibold">{plan.totals.action_count}</div>
              </div>
            </div>
            {plan.warnings.map((w, i) => (
              <div key={i} className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
                <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {w}
              </div>
            ))}

            {/* ── Stage 2: approve → sliced execution ─────────────── */}
            <div className="rounded border border-gray-200 p-3 space-y-2">
              <div className="flex items-center gap-2 flex-wrap text-xs">
                {plan.status === 'draft' && (
                  <Button size="sm" onClick={approvePlan}
                    disabled={busy === 'approve' || plan.feasible === false}
                    title={plan.feasible === false ? 'NOT FUNDED — cannot approve' : 'Approve for execution'}>
                    {busy === 'approve' ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : null}
                    Approve plan
                  </Button>
                )}
                {(plan.status === 'approved' || plan.status === 'executing') && (
                  <>
                    <span className="text-gray-500">Slice</span>
                    <select className="p-1.5 border border-gray-300 rounded-md text-xs" value={fraction}
                      onChange={(e) => setFraction(Number(e.target.value))}>
                      <option value={0.2}>20% (5-day stagger)</option>
                      <option value={0.25}>25% (4-day)</option>
                      <option value={0.34}>33% (3-day)</option>
                      <option value={0.5}>50% (2-day)</option>
                      <option value={1}>100% (all now)</option>
                    </select>
                    <label className="flex items-center gap-1 text-xs text-gray-700">
                      <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
                      dry run
                    </label>
                    <Button size="sm" onClick={executePlan} disabled={busy === 'execute'}
                      className={dryRun ? '' : 'bg-red-600 hover:bg-red-700 text-white'}>
                      {busy === 'execute' ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : null}
                      {dryRun ? 'Execute (dry run)' : 'PUNCH REAL ORDERS'}
                    </Button>
                    <Button size="sm" variant="outline" onClick={refreshFills} disabled={busy === 'fills'}>
                      {busy === 'fills' ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Refresh fills'}
                    </Button>
                  </>
                )}
                {plan.status === 'punched' && (
                  <>
                    <Badge className="bg-emerald-100 text-emerald-800">fully punched</Badge>
                    <Button size="sm" variant="outline" onClick={refreshFills} disabled={busy === 'fills'}>
                      Refresh fills
                    </Button>
                  </>
                )}
              </div>
              <p className="text-[11px] text-gray-500">
                Real orders additionally require <code>MH_TRADING_ENABLED=true</code> in .env, NSE market hours,
                and value caps (<code>MH_MAX_ORDER_VALUE</code> / <code>MH_MAX_EXECUTION_VALUE</code>). SELLs punch
                before BUYs. Validated policy: 20% per closing window over 5 days, no re-ranking mid-stagger.
              </p>
              {execResult && (
                <div className="text-xs space-y-1">
                  <div className="font-medium">
                    {execResult.dry_run ? 'Dry run — would send:' : `Punched ${execResult.punched}, failed ${execResult.failed}:`}
                    {execResult.note ? ` ${execResult.note}` : ''}
                  </div>
                  {(execResult.orders || execResult.results || []).map((o) => (
                    <div key={o.seq + o.symbol} className="flex justify-between border-b border-gray-50 py-0.5">
                      <span>{o.side} {o.symbol} × {o.qty} @ {o.price || 'MKT'}</span>
                      <span className={o.error ? 'text-red-600' : 'text-gray-500'}>
                        {o.error ?? (o.order_id ? `${o.order_id} · ${o.broker_status ?? ''}` : inr(o.est_value))}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="overflow-x-auto max-h-96 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-white/95">
                  <tr className="text-left text-[10px] uppercase text-gray-500 border-b">
                    <th className="py-1 pr-2">#</th>
                    <th className="py-1 pr-2">Action</th>
                    <th className="py-1 pr-2">Symbol</th>
                    <th className="py-1 pr-2 text-right">Qty</th>
                    <th className="py-1 pr-2 text-right">LTP</th>
                    <th className="py-1 pr-2 text-right">Limit</th>
                    <th className="py-1 pr-2 text-right">Est value</th>
                    <th className="py-1 pr-2 text-right" title="order value as % of 20-day avg daily traded value">%ADV</th>
                    <th className="py-1 pr-2 text-right">Live → target</th>
                    <th className="py-1 pr-2">Reason</th>
                    <th className="py-1">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.actions.map((a) => (
                    <tr key={a.seq} className="border-b border-gray-50">
                      <td className="py-1 pr-2 text-gray-400">{a.seq}</td>
                      <td className="py-1 pr-2">
                        <Badge className={a.action === 'BUY'
                          ? 'bg-emerald-50 text-emerald-700 border border-emerald-200'
                          : 'bg-red-50 text-red-700 border border-red-200'}>
                          {a.action}
                        </Badge>
                      </td>
                      <td className="py-1 pr-2 font-medium">{a.symbol}</td>
                      <td className="py-1 pr-2 text-right">{a.qty}</td>
                      <td className="py-1 pr-2 text-right">{a.last_price?.toFixed(2)}</td>
                      <td className="py-1 pr-2 text-right text-gray-500">{a.limit_price?.toFixed(2) ?? 'MKT'}</td>
                      <td className="py-1 pr-2 text-right">{inr(a.est_value)}</td>
                      <td className={(a.liquidity_capped ? 'text-amber-600 font-medium ' : 'text-gray-500 ') + 'py-1 pr-2 text-right'}>
                        {a.participation_percent !== null && a.participation_percent !== undefined ? a.participation_percent + '%' : '-'}
                        {a.liquidity_capped ? ' /' + a.suggested_slices + 'd' : ''}
                      </td>
                      <td className="py-1 pr-2 text-right text-gray-500">
                        {inr(a.live_value)} → {inr(a.target_value)}
                      </td>
                      <td className="py-1 pr-2 text-gray-500">{a.reason}{a.drift_percent !== null ? ` (${a.drift_percent}%)` : ''}</td>
                      <td className="py-1">
                        <Badge variant="outline">{a.status}</Badge>
                        {(a.punched_qty ?? 0) > 0 && (
                          <span className="text-[10px] text-gray-500 ml-1">
                            {a.punched_qty}/{a.qty}{(a.filled_qty ?? 0) > 0 ? ` · filled ${a.filled_qty}` : ''}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                  {plan.actions.length === 0 && (
                    <tr><td colSpan={11} className="py-3 text-gray-400">In sync — no actions needed.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
            {plan.skipped.length > 0 && (
              <details className="text-xs text-gray-500">
                <summary className="cursor-pointer">Skipped ({plan.skipped.length})</summary>
                <ul className="mt-1 space-y-0.5">
                  {plan.skipped.map((s, i) => (
                    <li key={i}><span className="font-medium">{s.symbol}</span> — {s.reason}</li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
