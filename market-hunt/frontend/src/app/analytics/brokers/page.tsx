'use client';

import React, { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { ArrowLeft, Landmark, RefreshCw, Loader2, ShieldCheck, LogIn, GitCompareArrows } from 'lucide-react';
import { inr, pct, Portfolio, portfolioApi } from '@/lib/portfolioApi';
import SyncPanel from '@/components/broker/SyncPanel';

const BASE = '/api';

interface BrokerStatus {
  kite: { configured: boolean; session_active: boolean; login_url: string | null };
  dhan: { configured: boolean; session_active: boolean };
}
interface BrokerHolding {
  symbol: string; exchange: string; quantity: number; avg_price: number;
  last_price: number | null; market_value: number | null; pnl: number | null; pnl_percent: number | null;
}
interface BrokerFunds { available_cash: number; margin_used: number | null; withdrawable: number | null }
interface DiffRow { symbol: string; sim_qty?: number; live_qty?: number; sim_value?: number; live_value?: number; value_drift?: number | null }
interface LiveVsSim {
  broker: string; sim_as_of: string; matched: DiffRow[]; sim_only: DiffRow[]; broker_only: DiffRow[];
  match_count: number; sim_only_count: number; broker_only_count: number;
}

function apiKeyHeaders(): Record<string, string> {
  const k = typeof window !== 'undefined' ? window.localStorage.getItem('mh_api_key') : null;
  return k ? { 'X-API-Key': k } : {};
}

async function bget<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`, { headers: apiKeyHeaders() });
  if (!r.ok) {
    const b = await r.json().catch(() => null);
    throw new Error(b && typeof b.detail === 'string' ? b.detail : `HTTP ${r.status}`);
  }
  return r.json();
}

export default function BrokersPage() {
  const [status, setStatus] = useState<BrokerStatus | null>(null);
  const [holdings, setHoldings] = useState<Record<string, BrokerHolding[]>>({});
  const [funds, setFunds] = useState<Record<string, BrokerFunds>>({});
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [diffPid, setDiffPid] = useState('');
  const [diffBroker, setDiffBroker] = useState('dhan');
  const [diff, setDiff] = useState<LiveVsSim | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [requestToken, setRequestToken] = useState('');

  const loadStatus = useCallback(() => {
    setBusy('status');
    bget<{ brokers: BrokerStatus }>('/broker/status')
      .then((d) => setStatus(d.brokers))
      .catch((e) => setError(e.message))
      .finally(() => setBusy(null));
  }, []);

  useEffect(() => {
    loadStatus();
    portfolioApi.list().then((d) => setPortfolios(d.portfolios || [])).catch(() => undefined);
  }, [loadStatus]);

  const loadBroker = async (name: 'kite' | 'dhan') => {
    setBusy(name);
    setError(null);
    try {
      const [h, f] = await Promise.all([
        bget<{ holdings: BrokerHolding[] }>(`/broker/${name}/holdings`),
        bget<{ funds: BrokerFunds }>(`/broker/${name}/funds`),
      ]);
      setHoldings((x) => ({ ...x, [name]: h.holdings }));
      setFunds((x) => ({ ...x, [name]: f.funds }));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'fetch failed');
    } finally {
      setBusy(null);
    }
  };

  const kiteSession = async () => {
    if (!requestToken.trim()) return setError('Paste the request_token from the Kite redirect URL.');
    setBusy('kite-session');
    setError(null);
    try {
      const r = await fetch(`${BASE}/broker/kite/session`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...apiKeyHeaders() },
        body: JSON.stringify({ request_token: requestToken.trim() }),
      });
      if (!r.ok) {
        const b = await r.json().catch(() => null);
        throw new Error(b?.detail || `HTTP ${r.status}`);
      }
      setRequestToken('');
      loadStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'session failed');
    } finally {
      setBusy(null);
    }
  };

  const runDiff = async () => {
    if (!diffPid) return;
    setBusy('diff');
    setError(null);
    try {
      setDiff(await bget<LiveVsSim>(`/portfolio/${diffPid}/live-vs-sim?broker=${diffBroker}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'diff failed');
    } finally {
      setBusy(null);
    }
  };

  const brokerCard = (name: 'kite' | 'dhan', title: string) => {
    const st = status?.[name];
    const h = holdings[name];
    const f = funds[name];
    return (
      <Card key={name}>
        <CardHeader className="pb-2 flex-row items-center justify-between space-y-0">
          <CardTitle className="text-base flex items-center gap-2">
            <Landmark className="w-4 h-4 text-indigo-600" /> {title}
            {st?.configured ? (
              st.session_active ? (
                <Badge className="bg-emerald-100 text-emerald-800">connected</Badge>
              ) : (
                <Badge className="bg-amber-100 text-amber-800">login needed</Badge>
              )
            ) : (
              <Badge variant="outline">not configured</Badge>
            )}
          </CardTitle>
          <Button size="sm" variant="outline" onClick={() => loadBroker(name)} disabled={!st?.configured || busy === name}>
            {busy === name ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          {!st?.configured && (
            <p className="text-xs text-gray-500">
              Add {name === 'kite' ? 'KITE_API_KEY / KITE_API_SECRET' : 'DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN'} to{' '}
              <code>market-hunt/.env</code> (see <code>.env.example</code>) and restart the backend. Keys never leave the
              server.
            </p>
          )}
          {name === 'kite' && st?.configured && !st.session_active && (
            <div className="space-y-2 text-xs">
              <p className="text-gray-600">
                Daily login: open{' '}
                <a className="text-indigo-600 underline" href={(st as { login_url?: string | null }).login_url ?? '#'} target="_blank" rel="noreferrer">
                  Kite login <LogIn className="w-3 h-3 inline" />
                </a>{' '}
                → after redirect, copy <code>request_token</code> from the URL and paste it here. The token is exchanged
                server-side and stored encrypted.
              </p>
              <div className="flex gap-2">
                <input
                  className="flex-1 p-1.5 border border-gray-300 rounded-md text-sm"
                  placeholder="request_token"
                  value={requestToken}
                  onChange={(e) => setRequestToken(e.target.value)}
                />
                <Button size="sm" onClick={kiteSession} disabled={busy === 'kite-session'}>
                  {busy === 'kite-session' ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Connect'}
                </Button>
              </div>
            </div>
          )}
          {f && (
            <div className="grid grid-cols-3 gap-2 text-sm">
              <div>
                <div className="text-[11px] text-gray-500">Available cash</div>
                <div className="font-semibold">{inr(f.available_cash)}</div>
              </div>
              <div>
                <div className="text-[11px] text-gray-500">Utilized</div>
                <div className="font-semibold">{f.margin_used !== null ? inr(f.margin_used) : '—'}</div>
              </div>
              <div>
                <div className="text-[11px] text-gray-500">Withdrawable</div>
                <div className="font-semibold">{f.withdrawable !== null ? inr(f.withdrawable) : '—'}</div>
              </div>
            </div>
          )}
          {h && (
            <div className="overflow-x-auto max-h-72 overflow-y-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-white/95">
                  <tr className="text-left text-[10px] uppercase text-gray-500 border-b">
                    <th className="py-1 pr-2">Symbol</th>
                    <th className="py-1 pr-2 text-right">Qty</th>
                    <th className="py-1 pr-2 text-right">Avg</th>
                    <th className="py-1 pr-2 text-right">LTP</th>
                    <th className="py-1 pr-2 text-right">Value</th>
                    <th className="py-1 text-right">P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {h.map((row) => (
                    <tr key={row.symbol} className="border-b border-gray-50">
                      <td className="py-1 pr-2 font-medium">{row.symbol}</td>
                      <td className="py-1 pr-2 text-right">{row.quantity}</td>
                      <td className="py-1 pr-2 text-right">{row.avg_price?.toFixed(2)}</td>
                      <td className="py-1 pr-2 text-right">{row.last_price?.toFixed(2) ?? '—'}</td>
                      <td className="py-1 pr-2 text-right">{row.market_value !== null ? inr(row.market_value) : '—'}</td>
                      <td className={`py-1 text-right ${(row.pnl ?? 0) >= 0 ? 'text-emerald-600' : 'text-red-600'}`}>
                        {row.pnl_percent !== null ? pct(row.pnl_percent) : '—'}
                      </td>
                    </tr>
                  ))}
                  {h.length === 0 && (
                    <tr><td colSpan={6} className="py-3 text-gray-400">No holdings.</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    );
  };

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <Link href="/analytics" className="text-gray-500 hover:text-gray-800">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <ShieldCheck className="w-6 h-6 text-indigo-600" />
          <div>
            <h1 className="text-xl font-bold text-gray-900">Brokers</h1>
            <p className="text-xs text-gray-500">
              Read-only connectivity (holdings · funds). Order placement is intentionally not wired — Stage 2.
            </p>
          </div>
        </div>
        <input
          className="p-1.5 border border-gray-300 rounded-md text-xs w-64"
          placeholder="X-API-Key (only if MH_API_TOKEN set)"
          defaultValue={typeof window !== 'undefined' ? window.localStorage.getItem('mh_api_key') ?? '' : ''}
          onChange={(e) => window.localStorage.setItem('mh_api_key', e.target.value)}
        />
      </div>

      {error && <div className="rounded-md border border-red-300 bg-red-50 px-4 py-2 text-sm text-red-700">{error}</div>}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        {brokerCard('dhan', 'Dhan (data: portfolio & funds)')}
        {brokerCard('kite', 'Zerodha Kite')}
      </div>

      <SyncPanel portfolios={portfolios} />

      <Card>
        <CardHeader className="pb-2 flex-row items-center justify-between space-y-0">
          <CardTitle className="text-base flex items-center gap-2">
            <GitCompareArrows className="w-4 h-4 text-indigo-600" /> Live vs simulation
          </CardTitle>
          <div className="flex items-center gap-2">
            <select className="p-1.5 border border-gray-300 rounded-md text-sm" value={diffPid} onChange={(e) => setDiffPid(e.target.value)}>
              <option value="">select portfolio…</option>
              {portfolios.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
            <select className="p-1.5 border border-gray-300 rounded-md text-sm" value={diffBroker} onChange={(e) => setDiffBroker(e.target.value)}>
              <option value="dhan">dhan</option>
              <option value="kite">kite</option>
            </select>
            <Button size="sm" onClick={runDiff} disabled={!diffPid || busy === 'diff'}>
              {busy === 'diff' ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Compare'}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {!diff && <p className="text-xs text-gray-500">Compares the broker&apos;s real holdings against the newest cached simulation book.</p>}
          {diff && (
            <div className="space-y-3">
              <div className="flex gap-2 text-xs">
                <Badge className="bg-emerald-100 text-emerald-800">{diff.match_count} matched</Badge>
                <Badge className="bg-amber-100 text-amber-800">{diff.sim_only_count} in sim only (to buy)</Badge>
                <Badge className="bg-red-100 text-red-800">{diff.broker_only_count} in broker only (not in sim)</Badge>
                <span className="text-gray-400">sim as of {diff.sim_as_of?.slice(0, 16).replace('T', ' ')}</span>
              </div>
              <div className="grid grid-cols-1 xl:grid-cols-3 gap-3 text-xs">
                <div>
                  <div className="font-medium mb-1">Matched (value drift)</div>
                  {diff.matched.map((r) => (
                    <div key={r.symbol} className="flex justify-between border-b border-gray-50 py-0.5">
                      <span>{r.symbol}</span>
                      <span className={r.value_drift && r.value_drift < 0 ? 'text-red-600' : 'text-gray-600'}>
                        {r.value_drift !== null && r.value_drift !== undefined ? inr(r.value_drift) : '—'}
                      </span>
                    </div>
                  ))}
                  {diff.matched.length === 0 && <span className="text-gray-400">none</span>}
                </div>
                <div>
                  <div className="font-medium mb-1">Sim only — needs buying</div>
                  {diff.sim_only.map((r) => (
                    <div key={r.symbol} className="flex justify-between border-b border-gray-50 py-0.5">
                      <span>{r.symbol}</span>
                      <span className="text-gray-600">{r.sim_value !== undefined ? inr(r.sim_value!) : '—'}</span>
                    </div>
                  ))}
                  {diff.sim_only.length === 0 && <span className="text-gray-400">none</span>}
                </div>
                <div>
                  <div className="font-medium mb-1">Broker only — not in sim</div>
                  {diff.broker_only.map((r) => (
                    <div key={r.symbol} className="flex justify-between border-b border-gray-50 py-0.5">
                      <span>{r.symbol}</span>
                      <span className="text-gray-600">{r.live_value !== undefined && r.live_value !== null ? inr(r.live_value) : '—'}</span>
                    </div>
                  ))}
                  {diff.broker_only.length === 0 && <span className="text-gray-400">none</span>}
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
