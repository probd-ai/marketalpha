'use client';

import React, { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Plus, Trash2, Copy, Save, Loader2 } from 'lucide-react';
import {
  Portfolio,
  Sleeve,
  StrategyOption,
  UniverseOption,
  ALPHA_STRATEGY_ID,
  EARLIEST_INCEPTION,
  alphaSleeve,
  defaultPortfolio,
  newSleeveId,
  portfolioApi,
} from '@/lib/portfolioApi';

const MOMENTUM_OPTIONS: { value: string; label: string }[] = [
  { value: '20_day_return', label: '20-Day Price Return' },
  { value: 'price_roc_66d', label: '66-Day Price Return' },
  { value: 'price_roc_222d', label: '222-Day Price Return' },
  { value: 'risk_adjusted', label: 'Risk-Adjusted Return (Sharpe-like)' },
  { value: 'technical', label: 'Technical Momentum (MA-based)' },
  { value: 'truevx_roc', label: 'TrueVX Score ROC' },
  { value: 'short_mean_roc', label: 'Short Mean ROC' },
  { value: 'mid_mean_roc', label: 'Mid Mean ROC' },
  { value: 'long_mean_roc', label: 'Long Mean ROC' },
  { value: 'stock_score_roc', label: 'StockScore ROC' },
];

const inputCls = 'w-full p-2 border border-gray-300 rounded-md text-sm bg-white/70';
const labelCls = 'block text-xs font-medium text-gray-600 mb-1';

interface Props {
  initial?: Portfolio; // present = edit mode
}

export default function PortfolioForm({ initial }: Props) {
  const router = useRouter();
  const editing = Boolean(initial?.id);
  const [portfolio, setPortfolio] = useState<Portfolio>(() => initial ?? defaultPortfolio());
  const [strategies, setStrategies] = useState<StrategyOption[]>([]);
  const [universes, setUniverses] = useState<UniverseOption[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    portfolioApi
      .strategies()
      .then((d) => {
        const list = d.strategies || [];
        setStrategies(list);
        // Prefill empty sleeve strategy with Alpha when available, else first saved strategy.
        const fallback = list.find((s) => s.id === ALPHA_STRATEGY_ID) ?? list[0];
        if (fallback) {
          setPortfolio((p) => ({
            ...p,
            sleeves: p.sleeves.map((s) => (s.strategy_id ? s : { ...s, strategy_id: fallback.id })),
          }));
        }
      })
      .catch((e) => setError(`Failed to load strategies: ${e.message}`));
    portfolioApi
      .universes()
      .then((d) => setUniverses(d.universes || []))
      .catch((e) => setError(`Failed to load universes: ${e.message}`));
  }, []);

  const weightSum = useMemo(
    () => Math.round(portfolio.sleeves.reduce((acc, s) => acc + (Number(s.weight) || 0), 0) * 100) / 100,
    [portfolio.sleeves]
  );
  const weightsOk = Math.abs(weightSum - 100) <= 0.5;

  const setField = <K extends keyof Portfolio>(key: K, value: Portfolio[K]) =>
    setPortfolio((p) => ({ ...p, [key]: value }));

  const setSleeve = (idx: number, patch: Partial<Sleeve>) =>
    setPortfolio((p) => ({
      ...p,
      sleeves: p.sleeves.map((s, i) => (i === idx ? { ...s, ...patch } : s)),
    }));

  const addSleeve = () =>
    setPortfolio((p) => ({
      ...p,
      sleeves: [
        ...p.sleeves,
        alphaSleeve({
          label: `Sleeve ${p.sleeves.length + 1}`,
          weight: 0,
          strategy_id: p.sleeves[0]?.strategy_id ?? '',
        }),
      ],
    }));

  const duplicateSleeve = (idx: number) =>
    setPortfolio((p) => {
      const src = p.sleeves[idx];
      return {
        ...p,
        sleeves: [...p.sleeves, { ...src, id: newSleeveId(), label: `${src.label} (copy)`, weight: 0 }],
      };
    });

  const removeSleeve = (idx: number) =>
    setPortfolio((p) => ({ ...p, sleeves: p.sleeves.filter((_, i) => i !== idx) }));

  const save = async () => {
    setError(null);
    if (!portfolio.name.trim()) return setError('Portfolio needs a name.');
    if (!weightsOk) return setError(`Sleeve weights must sum to 100 (currently ${weightSum}).`);
    if (portfolio.sleeves.some((s) => !s.strategy_id)) return setError('Every sleeve needs a strategy.');
    setSaving(true);
    try {
      if (editing && portfolio.id) {
        await portfolioApi.update(portfolio.id, portfolio);
        router.push(`/analytics/portfolios/${portfolio.id}`);
      } else {
        const res = await portfolioApi.create(portfolio);
        router.push(`/analytics/portfolios/${res.portfolio_id}`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Portfolio</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <div className="lg:col-span-2">
            <label className={labelCls}>Name</label>
            <input
              className={inputCls}
              value={portfolio.name}
              placeholder="e.g. Alpha Two-Tranche"
              onChange={(e) => setField('name', e.target.value)}
            />
          </div>
          <div>
            <label className={labelCls}>Total capital (₹)</label>
            <input
              type="number"
              min={10000}
              className={inputCls}
              value={portfolio.total_capital}
              onChange={(e) => setField('total_capital', Number(e.target.value))}
            />
          </div>
          <div>
            <label className={labelCls}>Inception date (≥ {EARLIEST_INCEPTION})</label>
            <input
              type="date"
              min={EARLIEST_INCEPTION}
              className={inputCls}
              value={portfolio.inception_date}
              onChange={(e) => setField('inception_date', e.target.value)}
            />
          </div>
          <div className="lg:col-span-2">
            <label className={labelCls}>Description</label>
            <input
              className={inputCls}
              value={portfolio.description}
              placeholder="optional"
              onChange={(e) => setField('description', e.target.value)}
            />
          </div>
          <div>
            <label className={labelCls}>Benchmark</label>
            <input
              className={inputCls}
              value={portfolio.benchmark_symbol}
              onChange={(e) => setField('benchmark_symbol', e.target.value)}
            />
          </div>
          <div className="flex items-end pb-2">
            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={portfolio.point_in_time_membership}
                onChange={(e) => setField('point_in_time_membership', e.target.checked)}
              />
              Point-in-time membership (survivorship-bias-free)
            </label>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle>
            Mini-portfolios (sleeves)
            <span className={`ml-3 text-sm font-normal ${weightsOk ? 'text-emerald-600' : 'text-red-600'}`}>
              weights: {weightSum}% {weightsOk ? '✓' : '→ must equal 100%'}
            </span>
          </CardTitle>
          <Button variant="outline" size="sm" onClick={addSleeve}>
            <Plus className="w-4 h-4 mr-1" /> Add sleeve
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          {portfolio.sleeves.map((s, idx) => (
            <div key={s.id} className="rounded-lg border border-gray-200 bg-white/50 p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <Badge variant="secondary">#{idx + 1}</Badge>
                  <input
                    className="p-1.5 border border-gray-300 rounded-md text-sm font-medium w-56"
                    value={s.label}
                    onChange={(e) => setSleeve(idx, { label: e.target.value })}
                  />
                </div>
                <div className="flex items-center gap-1">
                  <Button variant="ghost" size="sm" onClick={() => duplicateSleeve(idx)} title="Duplicate sleeve">
                    <Copy className="w-4 h-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeSleeve(idx)}
                    disabled={portfolio.sleeves.length <= 1}
                    title="Remove sleeve"
                  >
                    <Trash2 className="w-4 h-4 text-red-500" />
                  </Button>
                </div>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 gap-3">
                <div className="col-span-2">
                  <label className={labelCls}>Strategy</label>
                  <select
                    className={inputCls}
                    value={s.strategy_id}
                    onChange={(e) => setSleeve(idx, { strategy_id: e.target.value })}
                  >
                    <option value="" disabled>
                      select…
                    </option>
                    {strategies.map((st) => (
                      <option key={st.id} value={st.id}>
                        {st.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="col-span-2">
                  <label className={labelCls}>Universe</label>
                  <select
                    className={inputCls}
                    value={s.universe}
                    onChange={(e) => setSleeve(idx, { universe: e.target.value })}
                  >
                    {!universes.some((u) => u.value === s.universe) && (
                      <option value={s.universe}>{s.universe}</option>
                    )}
                    {universes.map((u) => (
                      <option key={u.value} value={u.value}>
                        {u.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className={labelCls}>Weight %</label>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    className={inputCls}
                    value={s.weight}
                    onChange={(e) => setSleeve(idx, { weight: Number(e.target.value) })}
                  />
                </div>
                <div>
                  <label className={labelCls}>Rebalance</label>
                  <select
                    className={inputCls}
                    value={s.rebalance_frequency}
                    onChange={(e) =>
                      setSleeve(idx, { rebalance_frequency: e.target.value as Sleeve['rebalance_frequency'] })
                    }
                  >
                    <option value="monthly">Monthly</option>
                    <option value="weekly">Weekly</option>
                    <option value="quarterly">Quarterly</option>
                  </select>
                </div>
                <div>
                  <label className={labelCls}>On day</label>
                  <select
                    className={inputCls}
                    value={s.rebalance_date}
                    onChange={(e) => setSleeve(idx, { rebalance_date: e.target.value as Sleeve['rebalance_date'] })}
                  >
                    <option value="first">First</option>
                    <option value="mid">Mid</option>
                    <option value="last">Last</option>
                  </select>
                </div>
                <div>
                  <label className={labelCls}>Weighting</label>
                  <select
                    className={inputCls}
                    value={s.rebalance_type}
                    onChange={(e) => setSleeve(idx, { rebalance_type: e.target.value as Sleeve['rebalance_type'] })}
                  >
                    <option value="equal_weight">Equal weight</option>
                    <option value="skewed">Skewed (holding period)</option>
                  </select>
                </div>
                <div>
                  <label className={labelCls}>Max holdings</label>
                  <input
                    type="number"
                    min={1}
                    max={200}
                    className={inputCls}
                    value={s.max_holdings}
                    onChange={(e) => setSleeve(idx, { max_holdings: Number(e.target.value) })}
                  />
                </div>
                <div className="col-span-2">
                  <label className={labelCls}>Momentum ranking</label>
                  <select
                    className={inputCls}
                    value={s.momentum_ranking}
                    onChange={(e) => setSleeve(idx, { momentum_ranking: e.target.value })}
                  >
                    {MOMENTUM_OPTIONS.map((m) => (
                      <option key={m.value} value={m.value}>
                        {m.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="col-span-2 md:col-span-4 lg:col-span-5 rounded border border-gray-100 bg-gray-50/50 p-2 grid grid-cols-2 md:grid-cols-6 gap-2">
                  <div className="col-span-2 md:col-span-6 text-[11px] font-medium text-gray-600">
                    Risk management — on/off gates (cash when risk-off)
                  </div>
                  <div>
                    <label className={labelCls}>Index gate</label>
                    <select className={inputCls}
                      value={s.regime_gate ? (s.gate_mode ?? 'sma') : 'off'}
                      onChange={(e) => {
                        const v = e.target.value;
                        setSleeve(idx, v === 'off'
                          ? { regime_gate: false }
                          : { regime_gate: true, gate_mode: v as 'sma' | 'dynamic_fib' });
                      }}>
                      <option value="off">Off</option>
                      <option value="sma">SMA</option>
                      <option value="dynamic_fib">Dynamic Fib</option>
                    </select>
                  </div>
                  <div>
                    <label className={labelCls}>Gate index</label>
                    <input className={inputCls} value={s.gate_symbol ?? 'Nifty 500'}
                      onChange={(e) => setSleeve(idx, { gate_symbol: e.target.value })} />
                  </div>
                  {(s.gate_mode ?? 'sma') === 'sma' ? (
                    <div>
                      <label className={labelCls}>SMA days</label>
                      <input type="number" min={20} className={inputCls} value={s.gate_sma ?? 200}
                        onChange={(e) => setSleeve(idx, { gate_sma: Number(e.target.value) })} />
                    </div>
                  ) : (
                    <>
                      <div>
                        <label className={labelCls}>Fib lookback</label>
                        <input type="number" min={30} className={inputCls} value={s.gate_fib_lookback ?? 222}
                          onChange={(e) => setSleeve(idx, { gate_fib_lookback: Number(e.target.value) })} />
                      </div>
                      <div>
                        <label className={labelCls}>Fib level</label>
                        <select className={inputCls} value={s.gate_fib_level ?? 0.66}
                          onChange={(e) => setSleeve(idx, { gate_fib_level: Number(e.target.value) })}>
                          <option value={0.33}>0.33</option>
                          <option value={0.5}>0.50</option>
                          <option value={0.66}>0.66</option>
                          <option value={0.77}>0.77</option>
                        </select>
                      </div>
                    </>
                  )}
                  <div className="flex items-end pb-1">
                    <label className="flex items-center gap-1.5 text-[11px] text-gray-700">
                      <input type="checkbox" checked={s.stock_gate ?? false}
                        onChange={(e) => setSleeve(idx, { stock_gate: e.target.checked })} />
                      Per-stock fib gate
                    </label>
                  </div>
                  <div className="flex items-end pb-1">
                    <label className="flex items-center gap-1.5 text-[11px] text-gray-700">
                      <input type="checkbox" checked={s.portfolio_gate ?? false}
                        onChange={(e) => setSleeve(idx, { portfolio_gate: e.target.checked })} />
                      Portfolio-curve gate
                    </label>
                  </div>
                </div>
                <div>
                  <label className={labelCls}>Slippage (bps/side)</label>
                  <input
                    type="number"
                    min={0}
                    max={200}
                    className={inputCls}
                    value={s.slippage_bps ?? 0}
                    onChange={(e) => setSleeve(idx, { slippage_bps: Number(e.target.value) })}
                  />
                </div>
                <div className="flex items-end pb-2">
                  <label className="flex items-center gap-2 text-xs text-gray-700">
                    <input
                      type="checkbox"
                      checked={s.include_brokerage}
                      onChange={(e) => setSleeve(idx, { include_brokerage: e.target.checked })}
                    />
                    Brokerage &amp; charges
                  </label>
                </div>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      <div className="flex items-center gap-3">
        <Button onClick={save} disabled={saving}>
          {saving ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
          {editing ? 'Save changes' : 'Create portfolio'}
        </Button>
        <Button variant="outline" onClick={() => router.back()} disabled={saving}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
