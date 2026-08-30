'use client';

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Plus, Layers, Trash2, ArrowLeft, Loader2, RefreshCw } from 'lucide-react';
import { Portfolio, portfolioApi, inr } from '@/lib/portfolioApi';
import { HoldingsStatus, SYNC_UI, TONE_CLASS, holdingsApi } from '@/lib/holdingsApi';

export default function PortfoliosPage() {
  const [portfolios, setPortfolios] = useState<Portfolio[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Live-holdings state per portfolio. Fetched separately and never allowed to fail
  // the page: reconciliation is secondary to listing the portfolios themselves.
  const [sync, setSync] = useState<Record<string, HoldingsStatus>>({});

  const load = () => {
    setLoading(true);
    setError(null);
    portfolioApi
      .list()
      .then((d) => {
        const list = d.portfolios || [];
        setPortfolios(list);
        list.forEach((p) => {
          if (!p.id) return;
          holdingsApi
            .status(p.id)
            .then((s) => setSync((prev) => ({ ...prev, [p.id as string]: s })))
            .catch(() => undefined); // never block the list on a holdings read
        });
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load portfolios'))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const remove = async (id: string, name: string) => {
    if (!window.confirm(`Delete portfolio "${name}"? This cannot be undone.`)) return;
    try {
      await portfolioApi.remove(id);
      setPortfolios((ps) => ps.filter((p) => p.id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  return (
    <div className="min-h-screen p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link href="/analytics" className="text-gray-500 hover:text-gray-800">
            <ArrowLeft className="w-5 h-5" />
          </Link>
          <Layers className="w-6 h-6 text-indigo-600" />
          <div>
            <h1 className="text-2xl font-bold text-gray-900">Portfolios</h1>
            <p className="text-sm text-gray-500">
              Multi-sleeve portfolios — each sleeve is a strategy + simulation parameters, composed point-in-time.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={load} disabled={loading}>
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
          <Link href="/analytics/portfolios/new">
            <Button>
              <Plus className="w-4 h-4 mr-2" /> New portfolio
            </Button>
          </Link>
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-24 text-gray-500">
          <Loader2 className="w-6 h-6 animate-spin mr-2" /> Loading portfolios…
        </div>
      ) : portfolios.length === 0 ? (
        <Card>
          <CardContent className="py-16 text-center space-y-4">
            <Layers className="w-10 h-10 text-gray-300 mx-auto" />
            <p className="text-gray-600">No portfolios yet.</p>
            <p className="text-sm text-gray-500 max-w-md mx-auto">
              A new portfolio starts with the validated Alpha two-tranche defaults: the same gate + 20-day-return
              top-25 sleeve on the mid-month and first-day schedules, 50/50.
            </p>
            <Link href="/analytics/portfolios/new">
              <Button>
                <Plus className="w-4 h-4 mr-2" /> Create your first portfolio
              </Button>
            </Link>
          </CardContent>
        </Card>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {portfolios.map((p) => (
            <Card key={p.id} className="hover:shadow-lg transition-shadow">
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between">
                  <CardTitle className="text-base">{p.name}</CardTitle>
                  <div className="flex items-center gap-1">
                    {p.point_in_time_membership && <Badge variant="secondary">PIT</Badge>}
                    {p.id && sync[p.id] && (
                      <span
                        title={sync[p.id].sync.message}
                        className={`text-[10px] px-1.5 py-0.5 rounded border ${
                          TONE_CLASS[SYNC_UI[sync[p.id].sync.state].tone]
                        }`}
                      >
                        {SYNC_UI[sync[p.id].sync.state].label}
                        {!!sync[p.id].gaps?.length && ` ${sync[p.id].gaps!.length}`}
                      </span>
                    )}
                  </div>
                </div>
                {p.description && <p className="text-xs text-gray-500">{p.description}</p>}
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <div className="text-xs text-gray-500">Capital</div>
                    <div className="font-medium">{inr(p.total_capital)}</div>
                  </div>
                  <div>
                    <div className="text-xs text-gray-500">Inception</div>
                    <div className="font-medium">{p.inception_date}</div>
                  </div>
                  <div className="col-span-2">
                    <div className="text-xs text-gray-500 mb-1">Sleeves ({p.sleeves.length})</div>
                    <div className="flex flex-wrap gap-1">
                      {p.sleeves.map((s) => (
                        <Badge key={s.id} variant="outline" className="text-xs">
                          {s.label} · {s.weight}%
                        </Badge>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 pt-1">
                  <Link href={`/analytics/portfolios/${p.id}`} className="flex-1">
                    <Button className="w-full" size="sm">
                      Open
                    </Button>
                  </Link>
                  <Link href={`/analytics/portfolios/${p.id}/edit`}>
                    <Button variant="outline" size="sm">
                      Edit
                    </Button>
                  </Link>
                  <Button variant="ghost" size="sm" onClick={() => p.id && remove(p.id, p.name)}>
                    <Trash2 className="w-4 h-4 text-red-500" />
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
