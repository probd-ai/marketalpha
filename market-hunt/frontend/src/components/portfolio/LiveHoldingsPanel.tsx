'use client';

// Maps a portfolio to a local folder of broker CSV exports and shows the
// reconciliation. There is no broker API connection by design -- the user downloads
// the export and drops it in the folder.
//
// Two things this UI must never do: imply a stale file is "fine", or present a
// broker position the strategy never targeted as a SELL. Both are handled by
// deferring entirely to the backend's state and vocabulary.

import React, { useCallback, useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { AlertTriangle, CheckCircle2, FolderSearch, Loader2, RefreshCw } from 'lucide-react';
import { HoldingsStatus, SYNC_UI, TONE_CLASS, holdingsApi } from '@/lib/holdingsApi';

export default function LiveHoldingsPanel({ portfolioId }: { portfolioId: string }) {
  const [status, setStatus] = useState<HoldingsStatus | null>(null);
  const [path, setPath] = useState('');
  const [pin, setPin] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(async () => {
    setErr(null);
    try {
      const s = await holdingsApi.status(portfolioId);
      setStatus(s);
      if (s.folder) setPath(s.folder);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to read holdings');
    }
  }, [portfolioId]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setBusy(true); setErr(null); setSaved(null);
    try {
      await holdingsApi.setConfig(portfolioId, path.trim(), pin);
      setSaved('Folder saved.');
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setBusy(false);
    }
  };

  // Discretionary is stored as a QUANTITY per symbol, so the whole map is written
  // each time rather than a per-symbol toggle.
  const writeDiscretionary = async (next: Record<string, number>) => {
    setBusy(true); setErr(null);
    try {
      await holdingsApi.setDiscretionary(portfolioId, next);
      await load();
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to save discretionary holdings');
    } finally {
      setBusy(false);
    }
  };

  const markDiscretionary = (symbol: string, quantity: number) =>
    writeDiscretionary({ ...(status?.discretionary || {}), [symbol]: quantity });

  const markAllDiscretionary = () => {
    const next = { ...(status?.discretionary || {}) };
    (status?.unclassified || []).forEach((u) => { next[u.symbol] = u.quantity; });
    return writeDiscretionary(next);
  };

  const unmark = (symbol: string) => {
    const next = { ...(status?.discretionary || {}) };
    delete next[symbol];
    return writeDiscretionary(next);
  };

  const ui = status ? SYNC_UI[status.sync.state] : null;
  const gaps = status?.gaps || [];
  const unclassified = status?.unclassified || [];

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base flex items-center gap-2">
            <FolderSearch className="w-4 h-4 text-indigo-600" /> Live holdings
          </CardTitle>
          <div className="flex items-center gap-2">
            {ui && (
              <span className={`text-xs px-2 py-0.5 rounded border ${TONE_CLASS[ui.tone]}`}>
                {ui.label}
              </span>
            )}
            <Button variant="outline" size="sm" onClick={load} disabled={busy}>
              <RefreshCw className="w-4 h-4" />
            </Button>
          </div>
        </div>
        <p className="text-xs text-gray-500">
          Download the holdings export from your broker into a folder and point this at it.
          Read fresh on every load; quantities only.
        </p>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="space-y-2">
          <label className="text-xs font-medium text-gray-600">Holdings folder (local path)</label>
          <div className="flex gap-2">
            <Input
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="C:\Users\you\marketalpha-holdings\alpha-market"
              className="font-mono text-xs"
            />
            <Button onClick={save} disabled={busy || !path.trim()} size="sm">
              {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Save'}
            </Button>
          </div>
          <label className="flex items-center gap-2 text-xs text-gray-600">
            <input type="checkbox" checked={pin} onChange={(e) => setPin(e.target.checked)} />
            Pin this broker account — later exports from a different account are refused
          </label>
          <p className="text-xs text-gray-400">
            Keep this folder outside the repository: broker exports contain personal details.
            Only quantities are stored.
          </p>
        </div>

        {err && (
          <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700">{err}</div>
        )}
        {saved && !err && (
          <div className="rounded border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
            {saved}
          </div>
        )}

        {status && (
          <div className={`rounded border px-3 py-2 text-xs ${TONE_CLASS[SYNC_UI[status.sync.state].tone]}`}>
            <div className="flex items-start gap-2">
              {SYNC_UI[status.sync.state].tone === 'good'
                ? <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0" />
                : <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />}
              <span>{status.sync.message}</span>
            </div>
          </div>
        )}

        {status?.source_file && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
            <Meta label="File" value={status.source_file} mono />
            <Meta label="Broker" value={(status.broker || '—').toUpperCase()} />
            <Meta
              label={status.as_of_source === 'broker' ? 'Broker timestamp' : 'File date'}
              value={(status.file_as_of || '—').replace('T', ' ').slice(0, 16)}
              hint={status.as_of_source === 'file_mtime'
                ? 'This broker states no export time, so the file date is used — weaker provenance.'
                : undefined}
            />
            <Meta label="System positions" value={String(Object.keys(status.target || {}).length)} />
          </div>
        )}

        {!!status?.unresolved?.length && (
          <div className="rounded border border-amber-200 bg-amber-50 p-3">
            <div className="text-xs font-medium text-amber-800 mb-1">
              {status.unresolved.length} name(s) need a symbol before anything can be compared
            </div>
            <div className="text-xs text-amber-700 font-mono leading-relaxed">
              {status.unresolved.join(' · ')}
            </div>
            <p className="text-xs text-amber-600 mt-2">
              This broker exports company names, not tickers. Ask the assistant to map them —
              nothing is guessed, because a wrong symbol becomes a wrong trade.
            </p>
          </div>
        )}

        {!!gaps.length && (
          <div>
            <div className="text-xs font-medium text-gray-600 mb-1">
              Gaps vs system book ({gaps.length})
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-gray-500 border-b">
                  <tr>
                    <th className="text-left py-1">Symbol</th>
                    <th className="text-left">Action</th>
                    <th className="text-right">Qty</th>
                    <th className="text-right">Target</th>
                    <th className="text-right">Live</th>
                  </tr>
                </thead>
                <tbody>
                  {gaps.map((g) => (
                    <tr key={g.symbol} className="border-b last:border-0">
                      <td className="py-1 font-medium">{g.symbol}</td>
                      <td className={g.action === 'BUY' ? 'text-emerald-700' : 'text-red-700'}>{g.action}</td>
                      <td className="text-right">{g.quantity}</td>
                      <td className="text-right text-gray-500">{g.target}</td>
                      <td className="text-right text-gray-500">{g.observed}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {!!unclassified.length && (
          <div>
            <div className="text-xs font-medium text-gray-600 mb-1">
              Held live, not in the system book ({unclassified.length})
            </div>
            <p className="text-xs text-gray-500 mb-2">
              These are not sell instructions — they may be your own positions. Marking one
              as discretionary records its QUANTITY, so if the strategy later picks the same
              symbol the arithmetic still works.
            </p>
            <div className="space-y-1">
              {unclassified.map((u) => (
                <div key={u.symbol} className="flex items-center gap-2 text-xs">
                  <span className="font-medium w-28">{u.symbol}</span>
                  <span className="text-gray-500 w-16 text-right">{u.quantity}</span>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() => markDiscretionary(u.symbol, u.quantity)}
                  >
                    Mine, not the strategy&apos;s
                  </Button>
                </div>
              ))}
              {unclassified.length > 1 && (
                <Button size="sm" variant="outline" disabled={busy} onClick={markAllDiscretionary}>
                  Mark all {unclassified.length} as discretionary
                </Button>
              )}
            </div>
          </div>
        )}

        {!!Object.keys(status?.discretionary || {}).length && (
          <details className="text-xs">
            <summary className="cursor-pointer text-gray-600 font-medium">
              Discretionary — yours, excluded from gaps
              {` (${Object.keys(status!.discretionary!).length})`}
            </summary>
            <div className="mt-2 space-y-1">
              {Object.entries(status!.discretionary!).map(([sym, qty]) => (
                <div key={sym} className="flex items-center gap-2">
                  <span className="font-medium w-28">{sym}</span>
                  <span className="text-gray-500 w-16 text-right">{qty}</span>
                  <Button size="sm" variant="ghost" disabled={busy} onClick={() => unmark(sym)}>
                    Remove
                  </Button>
                </div>
              ))}
            </div>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

function Meta({ label, value, mono, hint }: { label: string; value: string; mono?: boolean; hint?: string }) {
  return (
    <div title={hint}>
      <div className="text-gray-500">{label}</div>
      <div className={`font-medium text-gray-800 ${mono ? 'font-mono text-[11px] break-all' : ''}`}>{value}</div>
      {hint && <div className="text-[10px] text-amber-600 mt-0.5">weaker provenance</div>}
    </div>
  );
}
