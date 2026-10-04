'use client';

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Badge } from '@/components/ui/Badge';
import {
  TrendingUp,
  Play,
  Home,
  ArrowLeft,
  Settings,
  Plus,
  Target,
  Activity,
  BarChart3,
  Calendar,
  DollarSign,
  Edit2,
  Trash2,
  MoreVertical,
  Layers,
  ChevronDown,
  ChevronRight
} from 'lucide-react';
import { api } from '@/lib/api';

// Types for strategy and simulation
interface StrategyRule {
  id: string;
  metric: 'truevx_score' | 'mean_short' | 'mean_mid' | 'mean_long';
  operator: '>' | '<' | '>=' | '<=' | '==' | '!=';
  threshold: number;
  name: string;
}

interface Strategy {
  id: string;
  name: string;
  description: string;
  rules: StrategyRule[];
  // Optional persisted wave block + rich filter (snake_case, as stored/returned by backend).
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  wave?: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  filter?: any;
  // Book strategy: runs the fund desk engine itself (rules empty, no filter/wave).
  book?: BookBlock | null;
  createdAt: string;
  lastModified: string;
}

// ── Book engine types (docs/specs/book-strategies-in-simulator.md) ──────────
type BookKind = 'grid10' | 'tranche';

interface BookBlock {
  kind: BookKind;
  params?: Record<string, number | boolean>;
}

const BOOK_KIND_LABELS: Record<BookKind, string> = {
  grid10: 'Grid-10 value core',
  tranche: 'Weekly tranches',
};

const BOOK_KIND_DESCRIPTIONS: Record<BookKind, string> = {
  grid10: 'Hold 10 Nifty names: buy the lowest 22-day return among names whose mean_long grid turned up; sell only when the grid turns down and the last month was positive; never trims.',
  tranche: 'Each week buy the top-N Alpha-eligible names by 22-day return as tranches held N weeks, capped per name.',
};

// Engine defaults — the backend fills omitted params with the same values.
const BOOK_DEFAULTS = { n: 10, top: 5, weeks: 8, slot_div: 40, max_w: 0.10, deploy_cash: true };

// Validated point-in-time floor for book runs (backend MODEL_START_FLOOR).
const BOOK_START_FLOOR = '2020-10-01';

const WEEKDAY_OPTIONS: { value: number; label: string }[] = [
  { value: 0, label: 'Monday' },
  { value: 1, label: 'Tuesday' },
  { value: 2, label: 'Wednesday' },
  { value: 3, label: 'Thursday' },
  { value: 4, label: 'Friday' },
];

// One-line summary of a saved book's params for cards/previews.
const describeBookParams = (book: BookBlock): string => {
  const p = book.params ?? {};
  if (book.kind === 'grid10') return `${p.n ?? BOOK_DEFAULTS.n} names`;
  const maxW = typeof p.max_w === 'number' ? p.max_w : BOOK_DEFAULTS.max_w;
  return `top ${p.top ?? BOOK_DEFAULTS.top} · ${p.weeks ?? BOOK_DEFAULTS.weeks} wk hold · slot ÷${p.slot_div ?? BOOK_DEFAULTS.slot_div}`
    + ` · cap ${(maxW * 100).toFixed(0)}% · ${(p.deploy_cash ?? BOOK_DEFAULTS.deploy_cash) ? 'deploy cash' : 'no cash deploy'}`;
};

interface SimulationConfig {
  strategyId: string;
  portfolioBaseValue: number;
  rebalanceFrequency: 'monthly' | 'weekly' | 'quarterly' | 'dynamic';
  rebalanceDate: 'first' | 'last' | 'mid';
  rebalanceType: 'equal_weight' | 'skewed'; // Allocation method
  universe: string; // Dynamic universe from database
  benchmarkSymbol?: string; // Optional custom benchmark symbol
  maxHoldings: number; // Maximum number of stocks in portfolio (ignored when momentumRanking === 'none')
  // 'none' = no rank cut: hold every name that reaches this stage (replaces the old
  // "use all eligible" checkbox, so the cap and the ranker can no longer contradict).
  momentumRanking: 'none' |
                   '20_day_return' | 'price_roc_22d' | 'price_roc_66d' | 'price_roc_222d' | 'risk_adjusted' | 'technical' |
                   'truevx_roc' | 'short_mean_roc' | 'mid_mean_roc' | 'long_mean_roc' | 'stock_score_roc' |
                   'price_roc_22d_low' | 'price_roc_222d_low'; // Momentum ranking method ("_low" = lowest ROC, buy-the-dip)
  // Primary filter: an all-time-high / all-time-low band applied AFTER the strategy
  // filter and BEFORE the momentum ranking. `take` is the master switch.
  primaryFilterTake: 'none' | 'top50' | 'bottom50' | 'all';
  primaryFilterMetric: 'ath' | 'atl';
  primaryFilterBand: number; // percent distance from the extreme
  startDate: string;
  endDate: string;
  pointInTimeMembership: boolean; // Gate universe to index membership as-of each rebalance (survivorship-bias-free)
  regimeGate: boolean; // Flatten to cash while the gate index closes below its SMA; re-enter on recovery (long-only)
  gateSymbol: string; // Gate index (e.g. "Nifty 500")
  gateSma: number; // SMA length for the gate (trading days)
  bookWeekday: number; // Book strategies only: weekly rebalance session, 0 = Mon .. 4 = Fri
}

// ── Wave builder types ──────────────────────────────────────────────────────
type WaveMetric = 'mean_short' | 'mean_mid' | 'mean_long' | 'truevx_score';
type WaveDirection = 'up' | 'down';
type WaveOp = '>' | '<' | '>=' | '<=' | '==' | '!=';
type WaveKind = 'cross' | 'compare' | 'last_cross_dir';
type RhsKind = 'metric' | 'number';

interface WaveSignalRow {
  id: string;
  kind: WaveKind;
  metric: WaveMetric;
  level: number;
  direction: WaveDirection;
  grid: number[];
  lhsMetric: WaveMetric;
  op: WaveOp;
  rhsKind: RhsKind;
  rhsMetric: WaveMetric;
  rhsNumber: number;
  negate: boolean;
}

type WaveSignalGroup = WaveSignalRow[];

interface SignalGroupEditorProps {
  groups: WaveSignalGroup[];
  setGroups: React.Dispatch<React.SetStateAction<WaveSignalGroup[]>>;
  label: string;
  allowedKinds?: WaveKind[]; // which signal kinds the kind-selector offers (default: all three)
}

const WAVE_METRIC_OPTIONS: { value: WaveMetric; label: string }[] = [
  { value: 'mean_short', label: 'Short Mean' },
  { value: 'mean_mid', label: 'Mid Mean' },
  { value: 'mean_long', label: 'Long Mean' },
  { value: 'truevx_score', label: 'TrueVX Score' },
];

const WAVE_OP_OPTIONS: { value: WaveOp; label: string }[] = [
  { value: '>', label: '>' },
  { value: '<', label: '<' },
  { value: '>=', label: '>=' },
  { value: '<=', label: '<=' },
  { value: '==', label: '==' },
  { value: '!=', label: '!=' },
];

const makeSignalRow = (): WaveSignalRow => ({
  id: `sig_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
  kind: 'cross',
  metric: 'mean_long',
  level: 50,
  direction: 'up',
  grid: [50, 60, 70],
  lhsMetric: 'truevx_score',
  op: '>',
  rhsKind: 'number',
  rhsMetric: 'mean_long',
  rhsNumber: 50,
  negate: false,
});

// Deserialize a saved wave block's signal (backend snake_case shape) back into a
// builder row, so editing a saved wave strategy shows its real configuration.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const rowFromSignal = (sig: any): WaveSignalRow => {
  const base = makeSignalRow();
  if (sig?.kind === 'compare') {
    const rhsKind: RhsKind = sig.rhs && sig.rhs.metric != null ? 'metric' : 'number';
    return {
      ...base, kind: 'compare', lhsMetric: sig.lhs_metric ?? base.lhsMetric, op: sig.op ?? base.op,
      rhsKind, rhsMetric: sig.rhs?.metric ?? base.rhsMetric,
      rhsNumber: sig.rhs?.number ?? base.rhsNumber, negate: !!sig.negate,
    };
  }
  if (sig?.kind === 'last_cross_dir') {
    return {
      ...base, kind: 'last_cross_dir', metric: sig.metric ?? base.metric,
      grid: Array.isArray(sig.grid) ? sig.grid : base.grid,
      direction: sig.direction ?? base.direction, negate: !!sig.negate,
    };
  }
  // cross (default)
  return {
    ...base, kind: 'cross', metric: sig.metric ?? base.metric, level: sig.level ?? base.level,
    direction: sig.direction ?? base.direction, negate: !!sig.negate,
  };
};

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const groupsFromBlock = (gs: any): WaveSignalGroup[] =>
  Array.isArray(gs) ? gs.map((g) => (Array.isArray(g) ? g.map(rowFromSignal) : [])) : [];

// Serialize a builder row to the backend signal shape (snake_case). Shared by the
// wave block and the stateless filter block.
const serializeSignalRow = (row: WaveSignalRow): object => {
  if (row.kind === 'cross') {
    return { kind: 'cross', metric: row.metric, level: row.level, direction: row.direction, negate: row.negate };
  }
  if (row.kind === 'compare') {
    const rhs = row.rhsKind === 'metric' ? { metric: row.rhsMetric } : { number: row.rhsNumber };
    return { kind: 'compare', lhs_metric: row.lhsMetric, op: row.op, rhs, negate: row.negate };
  }
  return { kind: 'last_cross_dir', metric: row.metric, grid: row.grid, direction: row.direction, negate: row.negate };
};

const serializeGroups = (gs: WaveSignalGroup[]): object[][] =>
  gs.filter(g => g.length > 0).map(g => g.map(serializeSignalRow));

const KIND_LABELS: Record<WaveKind, string> = {
  cross: 'Cross level',
  compare: 'Compare metrics',
  last_cross_dir: 'Last cross direction',
};

// Editable comma-separated grid (e.g. "50, 60, 70"). Holds raw text while typing so
// commas/edits aren't fought by per-keystroke parsing; commits to number[] on blur/Enter.
const GridInput: React.FC<{ value: number[]; onCommit: (g: number[]) => void }> = ({ value, onCommit }) => {
  const [text, setText] = React.useState(value.join(', '));
  React.useEffect(() => { setText(value.join(', ')); }, [value]);
  const commit = () => {
    onCommit(text.split(',').map(v => parseFloat(v.trim())).filter(v => !isNaN(v)));
  };
  return (
    <input
      type="text"
      value={text}
      onChange={e => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); commit(); } }}
      placeholder="50, 60, 70"
      className="w-28 p-1.5 border border-gray-300 rounded text-xs"
    />
  );
};

const SignalGroupEditor: React.FC<SignalGroupEditorProps> = ({ groups, setGroups, label, allowedKinds }) => {
  const kinds: WaveKind[] = allowedKinds && allowedKinds.length > 0 ? allowedKinds : ['cross', 'compare', 'last_cross_dir'];
  // A new row defaults to the first allowed kind (so a filter editor starts on 'compare', not 'cross').
  const newRow = (): WaveSignalRow => ({ ...makeSignalRow(), kind: kinds[0] });
  const addGroup = () => setGroups(prev => [...prev, [newRow()]]);

  const removeGroup = (gi: number) =>
    setGroups(prev => prev.filter((_, i) => i !== gi));

  const addRow = (gi: number) =>
    setGroups(prev =>
      prev.map((g, i) => (i === gi ? [...g, newRow()] : g))
    );

  const removeRow = (gi: number, ri: number) =>
    setGroups(prev =>
      prev.map((g, i) => (i === gi ? g.filter((_, j) => j !== ri) : g))
    );

  const updateRow = (gi: number, ri: number, patch: Partial<WaveSignalRow>) =>
    setGroups(prev =>
      prev.map((g, i) =>
        i === gi ? g.map((r, j) => (j === ri ? { ...r, ...patch } : r)) : g
      )
    );

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-gray-700">{label}</span>
        <button
          type="button"
          onClick={addGroup}
          className="text-xs text-blue-600 hover:text-blue-800 flex items-center gap-1"
        >
          <Plus className="h-3 w-3" /> Add OR-group
        </button>
      </div>
      {groups.length === 0 && (
        <p className="text-xs text-gray-400 italic">No signal groups — click &quot;Add OR-group&quot; to start.</p>
      )}
      {groups.map((group, gi) => (
        <div key={gi} className="border border-blue-100 rounded-lg p-3 bg-blue-50/30 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-blue-700">
              OR-group {gi + 1}
              {group.length > 1 && <span className="ml-1 text-blue-500">(ALL must match — AND)</span>}
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => addRow(gi)}
                className="text-xs text-blue-600 hover:text-blue-800 flex items-center gap-1"
              >
                <Plus className="h-3 w-3" /> AND
              </button>
              <button
                type="button"
                onClick={() => removeGroup(gi)}
                className="text-xs text-red-500 hover:text-red-700"
              >
                Remove group
              </button>
            </div>
          </div>
          {group.map((row, ri) => (
            <div key={row.id} className="border border-gray-200 rounded-md p-2 bg-white space-y-2">
              <div className="flex items-center gap-2 flex-wrap">
                {/* Kind */}
                <select
                  value={row.kind}
                  onChange={e => updateRow(gi, ri, { kind: e.target.value as WaveKind })}
                  className="p-1.5 border border-gray-300 rounded text-xs"
                >
                  {kinds.map(k => (
                    <option key={k} value={k}>{KIND_LABELS[k]}</option>
                  ))}
                </select>

                {/* Fields for "cross" */}
                {row.kind === 'cross' && (
                  <>
                    <select
                      value={row.metric}
                      onChange={e => updateRow(gi, ri, { metric: e.target.value as WaveMetric })}
                      className="p-1.5 border border-gray-300 rounded text-xs"
                    >
                      {WAVE_METRIC_OPTIONS.map(o => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                    <span className="text-xs text-gray-500">crosses</span>
                    <select
                      value={row.direction}
                      onChange={e => updateRow(gi, ri, { direction: e.target.value as WaveDirection })}
                      className="p-1.5 border border-gray-300 rounded text-xs"
                    >
                      <option value="up">up through</option>
                      <option value="down">down through</option>
                    </select>
                    <input
                      type="number"
                      value={row.level}
                      onChange={e => updateRow(gi, ri, { level: parseFloat(e.target.value) || 0 })}
                      className="w-16 p-1.5 border border-gray-300 rounded text-xs"
                    />
                  </>
                )}

                {/* Fields for "compare" */}
                {row.kind === 'compare' && (
                  <>
                    <select
                      value={row.lhsMetric}
                      onChange={e => updateRow(gi, ri, { lhsMetric: e.target.value as WaveMetric })}
                      className="p-1.5 border border-gray-300 rounded text-xs"
                    >
                      {WAVE_METRIC_OPTIONS.map(o => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                    <select
                      value={row.op}
                      onChange={e => updateRow(gi, ri, { op: e.target.value as WaveOp })}
                      className="p-1.5 border border-gray-300 rounded text-xs"
                    >
                      {WAVE_OP_OPTIONS.map(o => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                    <select
                      value={row.rhsKind}
                      onChange={e => updateRow(gi, ri, { rhsKind: e.target.value as RhsKind })}
                      className="p-1.5 border border-gray-300 rounded text-xs"
                    >
                      <option value="number">number</option>
                      <option value="metric">metric</option>
                    </select>
                    {row.rhsKind === 'number' ? (
                      <input
                        type="number"
                        value={row.rhsNumber}
                        onChange={e => updateRow(gi, ri, { rhsNumber: parseFloat(e.target.value) || 0 })}
                        className="w-16 p-1.5 border border-gray-300 rounded text-xs"
                      />
                    ) : (
                      <select
                        value={row.rhsMetric}
                        onChange={e => updateRow(gi, ri, { rhsMetric: e.target.value as WaveMetric })}
                        className="p-1.5 border border-gray-300 rounded text-xs"
                      >
                        {WAVE_METRIC_OPTIONS.map(o => (
                          <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                      </select>
                    )}
                  </>
                )}

                {/* Fields for "last_cross_dir" */}
                {row.kind === 'last_cross_dir' && (
                  <>
                    <select
                      value={row.metric}
                      onChange={e => updateRow(gi, ri, { metric: e.target.value as WaveMetric })}
                      className="p-1.5 border border-gray-300 rounded text-xs"
                    >
                      {WAVE_METRIC_OPTIONS.map(o => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                    <span className="text-xs text-gray-500">last crossed</span>
                    <select
                      value={row.direction}
                      onChange={e => updateRow(gi, ri, { direction: e.target.value as WaveDirection })}
                      className="p-1.5 border border-gray-300 rounded text-xs"
                    >
                      <option value="up">up</option>
                      <option value="down">down</option>
                    </select>
                    <span className="text-xs text-gray-500">grid:</span>
                    <GridInput value={row.grid} onCommit={g => updateRow(gi, ri, { grid: g })} />
                  </>
                )}

                {/* Negate toggle */}
                <label className="flex items-center gap-1 text-xs text-gray-600 ml-auto">
                  <input
                    type="checkbox"
                    checked={row.negate}
                    onChange={e => updateRow(gi, ri, { negate: e.target.checked })}
                    className="h-3 w-3 rounded border-gray-300"
                  />
                  NOT
                </label>

                {group.length > 1 && (
                  <button
                    type="button"
                    onClick={() => removeRow(gi, ri)}
                    className="text-xs text-red-400 hover:text-red-600 ml-1"
                  >
                    ✕
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
};

// ── Page component ─────────────────────────────────────────────────────────
const AnalyticsSimulatorPage = () => {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [selectedStrategy, setSelectedStrategy] = useState<Strategy | null>(null);
  const [showCreateStrategy, setShowCreateStrategy] = useState(false);
  const [showSimulationConfig, setShowSimulationConfig] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [universeOptions, setUniverseOptions] = useState<Array<{value: string, label: string, stock_count: number, pit_eligible?: boolean}>>([]);

  // Edit and Delete states
  const [editingStrategy, setEditingStrategy] = useState<Strategy | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);

  // Strategy creation states
  const [newStrategyName, setNewStrategyName] = useState('');
  const [newStrategyDescription, setNewStrategyDescription] = useState('');
  const [newStrategyRules, setNewStrategyRules] = useState<StrategyRule[]>([]);

  // Simulation config states
  const [simulationConfig, setSimulationConfig] = useState<SimulationConfig>({
    strategyId: '',
    portfolioBaseValue: 100000,
    rebalanceFrequency: 'monthly',
    rebalanceDate: 'first',
    rebalanceType: 'equal_weight', // Default to equal weight allocation
    universe: 'NIFTY50', // Default fallback, will be updated when universes load
    benchmarkSymbol: '50 EQL Wgt', // Default to Equal Weight Index
    maxHoldings: 10, // Default maximum holdings (applies once a ranking method is chosen)
    momentumRanking: 'none', // Default: no rank cut — hold all eligible
    primaryFilterTake: 'none', // Default: primary filter off
    primaryFilterMetric: 'ath',
    primaryFilterBand: 5,
    startDate: '2020-01-01',
    endDate: '2025-08-30',
    pointInTimeMembership: false, // Default: current-snapshot universe (survivorship-biased)
    regimeGate: false, // Default: always invested
    gateSymbol: 'Nifty 500',
    gateSma: 200,
    bookWeekday: 0 // Monday
  });

  // Wave builder state
  const [waveOpen, setWaveOpen] = useState(false);
  const [capitalN, setCapitalN] = useState(1000000);
  const [tranches, setTranches] = useState(20);
  const [reentry, setReentry] = useState(true);
  const [exitMode, setExitMode] = useState<'full' | 'fractional'>('full');
  const [buyCadence, setBuyCadence] = useState<'daily' | 'weekly' | 'monthly'>('daily');
  const [buyDate, setBuyDate] = useState<'first' | 'last'>('first');
  const [entryGroups, setEntryGroups] = useState<WaveSignalGroup[]>([]);
  const [accumGroups, setAccumGroups] = useState<WaveSignalGroup[]>([]);
  const [exitGroups, setExitGroups] = useState<WaveSignalGroup[]>([]);

  // Rich stateless filter conditions (replaces the old flat rule rows).
  const [filterGroups, setFilterGroups] = useState<WaveSignalGroup[]>([]);

  const resetWaveState = () => {
    setWaveOpen(false);
    setCapitalN(1000000);
    setTranches(20);
    setReentry(true);
    setExitMode('full');
    setBuyCadence('daily');
    setBuyDate('first');
    setEntryGroups([]);
    setAccumGroups([]);
    setExitGroups([]);
  };

  // Book builder state. A book strategy is exclusive: while enabled, the filter and
  // wave inputs are disabled and never saved (the backend rejects the mix).
  const [bookOpen, setBookOpen] = useState(false);
  const [bookEnabled, setBookEnabled] = useState(false);
  const [bookKind, setBookKind] = useState<BookKind>('grid10');
  const [bookN, setBookN] = useState(BOOK_DEFAULTS.n);
  const [bookTop, setBookTop] = useState(BOOK_DEFAULTS.top);
  const [bookWeeks, setBookWeeks] = useState(BOOK_DEFAULTS.weeks);
  const [bookSlotDiv, setBookSlotDiv] = useState(BOOK_DEFAULTS.slot_div);
  const [bookMaxWPct, setBookMaxWPct] = useState(BOOK_DEFAULTS.max_w * 100); // shown as %, sent as a fraction
  const [bookDeployCash, setBookDeployCash] = useState(BOOK_DEFAULTS.deploy_cash);

  const resetBookState = () => {
    setBookOpen(false);
    setBookEnabled(false);
    setBookKind('grid10');
    setBookN(BOOK_DEFAULTS.n);
    setBookTop(BOOK_DEFAULTS.top);
    setBookWeeks(BOOK_DEFAULTS.weeks);
    setBookSlotDiv(BOOK_DEFAULTS.slot_div);
    setBookMaxWPct(BOOK_DEFAULTS.max_w * 100);
    setBookDeployCash(BOOK_DEFAULTS.deploy_cash);
  };

  // Same ranges as the fund desk's rule table (fund.config.PARAMS); null when valid.
  const bookParamError = (): string | null => {
    const intIn = (v: number, lo: number, hi: number) => Number.isInteger(v) && v >= lo && v <= hi;
    if (bookKind === 'grid10') {
      return intIn(bookN, 1, 50) ? null : 'Names must be a whole number 1–50.';
    }
    if (!intIn(bookTop, 1, 20)) return 'Top N must be a whole number 1–20.';
    if (!intIn(bookWeeks, 1, 52)) return 'Hold weeks must be a whole number 1–52.';
    if (!intIn(bookSlotDiv, 1, 1000)) return 'Slot divisor must be a whole number 1–1000.';
    if (!(bookMaxWPct >= 1 && bookMaxWPct <= 100)) return 'Per-name cap must be 1–100%.';
    return null;
  };

  const buildBookBlock = (): BookBlock | undefined => {
    if (!bookEnabled) return undefined;
    if (bookKind === 'grid10') return { kind: 'grid10', params: { n: bookN } };
    return {
      kind: 'tranche',
      params: {
        top: bookTop,
        weeks: bookWeeks,
        slot_div: bookSlotDiv,
        max_w: Math.round(bookMaxWPct * 100) / 10000, // % → fraction, no float noise
        deploy_cash: bookDeployCash,
      },
    };
  };

  const buildWaveBlock = (): object | undefined => {
    const totalEntrySignals = entryGroups.reduce((s, g) => s + g.length, 0);
    if (totalEntrySignals === 0) return undefined;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const wave: any = {
      capital_N: capitalN,
      tranches,
      reentry,
      buy_cadence: buyCadence,
      exit_mode: exitMode,
      entry: serializeGroups(entryGroups),
      accumulate: serializeGroups(accumGroups),
      exit: serializeGroups(exitGroups),
    };
    if (buyCadence !== 'daily') {
      wave.buy_date = buyDate;
    }
    return wave;
  };

  // Rich stateless filter (compare/last_cross_dir signal groups). undefined when empty.
  const buildFilterBlock = (): object[][] | undefined => {
    const groups = serializeGroups(filterGroups);
    return groups.length > 0 ? groups : undefined;
  };

  const filterSignalCount = () => filterGroups.reduce((s, g) => s + g.length, 0);

  const metricOptions = [
    { value: 'truevx_score', label: 'TrueVX Score', description: 'Main TrueValueX ranking score (0-100)' },
    { value: 'mean_short', label: 'Short Mean', description: 'Short-term (22-period) moving average' },
    { value: 'mean_mid', label: 'Mid Mean', description: 'Mid-term (66-period) moving average' },
    { value: 'mean_long', label: 'Long Mean', description: 'Long-term (222-period) moving average' },
  ];

  const operatorOptions = [
    { value: '>', label: 'Greater than (>)' },
    { value: '<', label: 'Less than (<)' },
    { value: '>=', label: 'Greater than or equal (>=)' },
    { value: '<=', label: 'Less than or equal (<=)' },
    { value: '==', label: 'Equal to (==)' },
    { value: '!=', label: 'Not equal to (!=)' },
  ];

  const benchmarkOptions = [
    { value: '50 EQL Wgt', label: '50 EQL Wgt (Equal Weight Index)' },
    { value: 'Nifty 50', label: 'Nifty 50 (Cap Weighted)' },
  ];

  const rebalanceTypeOptions = [
    { 
      value: 'equal_weight', 
      label: 'Equal Weight', 
      description: 'Equal allocation to all selected stocks' 
    },
    { 
      value: 'skewed', 
      label: 'Skewed (Holding Period)', 
      description: 'More allocation to stocks held for longer periods' 
    },
  ];

  // Load existing strategies and universes
  useEffect(() => {
    loadStrategies();
    loadUniverses();
  }, []);

  const loadStrategies = async () => {
    try {
      setIsLoading(true);
      
      // Load strategies from API
      const response = await api.getStrategies();
      if (response.success) {
        setStrategies(response.strategies || []);
      } else {
        console.error('Failed to load strategies:', response.error);
        setStrategies([]);
      }
    } catch (err) {
      console.error('Failed to load strategies:', err);
      setError('Failed to load strategies');
    } finally {
      setIsLoading(false);
    }
  };

  const loadUniverses = async () => {
    try {
      const response = await fetch('/api/data/universes');
      const data = await response.json();
      
      if (data.universes && Array.isArray(data.universes)) {
        setUniverseOptions(data.universes);
        console.log(`📊 Loaded ${data.universes.length} universes from database`);
      } else {
        // Fallback to hardcoded options if API fails
        console.warn('Failed to load universes from API, using fallback');
        setUniverseOptions([
          { value: 'NIFTY50', label: 'NIFTY 50 (50 stocks)', stock_count: 50, pit_eligible: true },
          { value: 'NIFTY100', label: 'NIFTY 100 (100 stocks)', stock_count: 100, pit_eligible: true },
          { value: 'NIFTY500', label: 'NIFTY 500 (500 stocks)', stock_count: 500, pit_eligible: true },
        ]);
      }
    } catch (err) {
      console.error('Failed to load universes:', err);
      // Fallback to hardcoded options
      setUniverseOptions([
        { value: 'NIFTY50', label: 'NIFTY 50 (50 stocks)', stock_count: 50, pit_eligible: true },
        { value: 'NIFTY100', label: 'NIFTY 100 (100 stocks)', stock_count: 100, pit_eligible: true },
        { value: 'NIFTY500', label: 'NIFTY 500 (500 stocks)', stock_count: 500, pit_eligible: true },
      ]);
    }
  };

  const addNewRule = () => {
    const newRule: StrategyRule = {
      id: `rule_${Date.now()}`,
      metric: 'truevx_score',
      operator: '>',
      threshold: 60,
      name: 'New Rule'
    };
    setNewStrategyRules([...newStrategyRules, newRule]);
  };

  const updateRule = (ruleId: string, updates: Partial<StrategyRule>) => {
    setNewStrategyRules(rules => 
      rules.map(rule => 
        rule.id === ruleId ? { ...rule, ...updates } : rule
      )
    );
  };

  const removeRule = (ruleId: string) => {
    setNewStrategyRules(rules => rules.filter(rule => rule.id !== ruleId));
  };

  const saveStrategy = async () => {
    try {
      setIsLoading(true);

      const bookBlock = buildBookBlock();
      const waveBlock = buildWaveBlock();
      const filterBlock = buildFilterBlock();
      // A book strategy carries only the book: rules empty, no filter, no wave.
      const strategyData = bookBlock
        ? { name: newStrategyName, description: newStrategyDescription, rules: [], book: bookBlock }
        : {
            name: newStrategyName,
            description: newStrategyDescription,
            // Rich filter supersedes flat rules; keep rules empty for filter/wave strategies.
            rules: filterBlock ? [] : newStrategyRules,
            ...(filterBlock ? { filter: filterBlock } : {}),
            ...(waveBlock ? { wave: waveBlock } : {}),
          };

      let response;
      if (editingStrategy) {
        // Update existing strategy
        response = await api.updateStrategy(editingStrategy.id, strategyData);
      } else {
        // Create new strategy
        response = await api.saveStrategy(strategyData);
      }
      
      if (response.success) {
        // Reload strategies to get the updated list
        await loadStrategies();
        
        // Reset form
        setNewStrategyName('');
        setNewStrategyDescription('');
        setNewStrategyRules([]);
        setFilterGroups([]);
        setEditingStrategy(null);
        setShowCreateStrategy(false);
        resetWaveState();
        resetBookState();
      } else {
        setError(editingStrategy ? 'Failed to update strategy' : 'Failed to save strategy');
      }
      
    } catch (err) {
      console.error('Error saving strategy:', err);
      setError(editingStrategy ? 'Failed to update strategy' : 'Failed to save strategy');
    } finally {
      setIsLoading(false);
    }
  };

  const startSimulation = (strategy: Strategy) => {
    setSelectedStrategy(strategy);
    if (strategy.book) {
      // A book run is always point-in-time from the validated floor: snap the start
      // date forward and off any universe without reconstitution data.
      const current = universeOptions.find(o => o.value === simulationConfig.universe);
      const fallback = current && current.pit_eligible === false
        ? (universeOptions.find(o => o.value === 'NIFTY50' && o.pit_eligible) ?? universeOptions.find(o => o.pit_eligible))
        : undefined;
      setSimulationConfig({
        ...simulationConfig,
        strategyId: strategy.id,
        startDate: simulationConfig.startDate < BOOK_START_FLOOR ? BOOK_START_FLOOR : simulationConfig.startDate,
        universe: fallback ? fallback.value : simulationConfig.universe,
      });
    } else {
      setSimulationConfig({ ...simulationConfig, strategyId: strategy.id });
    }
    setShowSimulationConfig(true);
  };

  const editStrategy = (strategy: Strategy) => {
    setEditingStrategy(strategy);
    setNewStrategyName(strategy.name);
    setNewStrategyDescription(strategy.description);
    setNewStrategyRules(strategy.rules || []);
    // Hydrate the filter editor: from a saved rich filter, or by converting legacy
    // (metric,op,threshold) rules into one AND-group of compare-vs-number signals.
    if (strategy.filter && Array.isArray(strategy.filter) && strategy.filter.length > 0) {
      setFilterGroups(groupsFromBlock(strategy.filter));
    } else if (strategy.rules && strategy.rules.length > 0) {
      const grp = strategy.rules.map((r) => ({
        ...makeSignalRow(), kind: 'compare' as WaveKind,
        lhsMetric: r.metric as WaveMetric, op: r.operator as WaveOp,
        rhsKind: 'number' as RhsKind, rhsNumber: r.threshold,
      }));
      setFilterGroups([grp]);
    } else {
      setFilterGroups([]);
    }
    // Hydrate the wave builder from the saved wave block (if any), so the user
    // can see/edit it — otherwise re-saving would silently drop the wave.
    const w = strategy.wave;
    if (w && typeof w === 'object') {
      setWaveOpen(true);
      setCapitalN(w.capital_N ?? 1000000);
      setTranches(w.tranches ?? 20);
      setReentry(w.reentry ?? true);
      setExitMode(w.exit_mode === 'fractional' ? 'fractional' : 'full');
      setBuyCadence((['daily', 'weekly', 'monthly'].includes(w.buy_cadence) ? w.buy_cadence : 'daily') as 'daily' | 'weekly' | 'monthly');
      setBuyDate(w.buy_date === 'last' ? 'last' : 'first');
      setEntryGroups(groupsFromBlock(w.entry));
      setAccumGroups(groupsFromBlock(w.accumulate));
      setExitGroups(groupsFromBlock(w.exit));
    } else {
      resetWaveState();
    }
    // Hydrate the book section from the saved book block (same reason as the wave).
    const b = strategy.book;
    if (b && typeof b === 'object') {
      const p = b.params ?? {};
      const num = (v: unknown, d: number) => (typeof v === 'number' ? v : d);
      setBookOpen(true);
      setBookEnabled(true);
      setBookKind(b.kind === 'tranche' ? 'tranche' : 'grid10');
      setBookN(num(p.n, BOOK_DEFAULTS.n));
      setBookTop(num(p.top, BOOK_DEFAULTS.top));
      setBookWeeks(num(p.weeks, BOOK_DEFAULTS.weeks));
      setBookSlotDiv(num(p.slot_div, BOOK_DEFAULTS.slot_div));
      setBookMaxWPct(Math.round(num(p.max_w, BOOK_DEFAULTS.max_w) * 10000) / 100);
      setBookDeployCash(typeof p.deploy_cash === 'boolean' ? p.deploy_cash : BOOK_DEFAULTS.deploy_cash);
    } else {
      resetBookState();
    }
    setShowCreateStrategy(true);
  };

  const deleteStrategy = (strategyId: string) => {
    setShowDeleteConfirm(strategyId);
  };

  const confirmDeleteStrategy = async () => {
    if (!showDeleteConfirm) return;

    setIsDeleting(true);
    try {
      const response = await api.deleteStrategy(showDeleteConfirm);
      
      if (response.success) {
        // Refresh strategies list
        loadStrategies();
        setShowDeleteConfirm(null);
      } else {
        console.error('Failed to delete strategy');
        alert('Failed to delete strategy. Please try again.');
      }
    } catch (error) {
      console.error('Error deleting strategy:', error);
      alert('Error deleting strategy. Please try again.');
    } finally {
      setIsDeleting(false);
    }
  };

  const cancelDelete = () => {
    setShowDeleteConfirm(null);
  };

  const runSimulation = () => {
    // Navigate to simulation runner page
    const params = new URLSearchParams({
      strategyId: simulationConfig.strategyId,
      portfolioValue: simulationConfig.portfolioBaseValue.toString(),
      rebalanceFreq: simulationConfig.rebalanceFrequency,
      rebalanceDate: simulationConfig.rebalanceDate,
      rebalanceType: simulationConfig.rebalanceType, // Add rebalance type parameter
      universe: simulationConfig.universe,
      benchmarkSymbol: simulationConfig.benchmarkSymbol || '50 EQL Wgt', // Always include benchmark
      maxHoldings: simulationConfig.maxHoldings.toString(),
      momentumRanking: simulationConfig.momentumRanking,
      primaryFilterTake: simulationConfig.primaryFilterTake,
      primaryFilterMetric: simulationConfig.primaryFilterMetric,
      primaryFilterBand: simulationConfig.primaryFilterBand.toString(),
      startDate: simulationConfig.startDate,
      endDate: simulationConfig.endDate,
      pointInTimeMembership: simulationConfig.pointInTimeMembership.toString(),
      regimeGate: simulationConfig.regimeGate.toString(),
      gateSymbol: simulationConfig.gateSymbol,
      gateSma: simulationConfig.gateSma.toString()
    });

    // A book strategy defines its own schedule and selection: weekly, always
    // point-in-time, no primary filter or gate (the backend 400s otherwise).
    // `bookWeekday` in the URL is what marks the run as a book run downstream.
    if (selectedStrategy?.book) {
      params.set('rebalanceFreq', 'weekly');
      params.set('pointInTimeMembership', 'true');
      params.set('primaryFilterTake', 'none');
      params.set('regimeGate', 'false');
      params.set('bookWeekday', simulationConfig.bookWeekday.toString());
    }

    // A run executes the SELECTED saved strategy: the backend reads that
    // strategy's persisted wave block by id (a saved wave strategy runs as a
    // wave automatically). We deliberately do NOT attach the builder's wave
    // here — that would let leftover builder state override the chosen strategy.
    try {
      sessionStorage.removeItem('pendingWaveConfig');
    } catch {
      // sessionStorage unavailable — nothing to clear
    }

    window.location.href = `/analytics/simulator/run?${params.toString()}`;
  };

  // The selected strategy runs on the book engine: the run form hides what the
  // book defines itself and enforces what the backend would otherwise reject.
  const isBook = !!selectedStrategy?.book;
  const bookStartTooEarly = isBook && simulationConfig.startDate < BOOK_START_FLOOR;
  const bookDefinedNote = (
    <p className="text-xs text-gray-500 italic">Defined by the book — not used for book strategies.</p>
  );
  const sweepDisabledTitle = 'Book strategies run on the fund desk engine; the multi-run sweep engine is stateless-only and refuses them.';

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center gap-4 mb-4">
            <Link href="/analytics" className="flex items-center gap-2 text-blue-600 hover:text-blue-800">
              <ArrowLeft className="h-5 w-5" />
              Analytics Hub
            </Link>
            <span className="text-gray-500">/</span>
            <span className="font-semibold text-gray-900">Strategy Simulator</span>
          </div>
          
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-3xl font-bold text-gray-900 mb-2">Strategy Simulator</h1>
              <p className="text-gray-600">Create and backtest trading strategies based on TrueValueX indicators</p>
            </div>
            
            <Button
              onClick={() => {
                // Start a clean new strategy — clear any leftover edit/form state.
                setEditingStrategy(null);
                setNewStrategyName('');
                setNewStrategyDescription('');
                setNewStrategyRules([]);
                setFilterGroups([]);
                resetWaveState();
                resetBookState();
                setShowCreateStrategy(true);
              }}
              className="flex items-center gap-2"
            >
              <Plus className="h-4 w-4" />
              Create Strategy
            </Button>
          </div>
        </div>

        {/* Strategies Grid */}
        {!showCreateStrategy && !showSimulationConfig && (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {strategies.map((strategy) => (
              <Card key={strategy.id} className="hover:shadow-lg transition-shadow">
                <CardHeader>
                  <CardTitle className="flex items-center justify-between">
                    <span>{strategy.name}</span>
                    <Badge variant="outline">
                      {strategy.book
                        ? `Book · ${BOOK_KIND_LABELS[strategy.book.kind] ?? strategy.book.kind}`
                        : strategy.wave
                        ? 'Wave'
                        : (Array.isArray(strategy.filter) && strategy.filter.length > 0)
                          ? `${(strategy.filter as WaveSignalRow[][]).reduce((s, g) => s + g.length, 0)} conditions`
                          : `${strategy.rules.length} rules`}
                    </Badge>
                  </CardTitle>
                  <p className="text-sm text-gray-600">{strategy.description}</p>
                </CardHeader>
                <CardContent>
                  <div className="space-y-3">
                    {/* Strategy preview */}
                    {strategy.rules.length > 0 ? (
                      <div className="space-y-2">
                        <h4 className="text-sm font-medium text-gray-700">Rules:</h4>
                        {strategy.rules.slice(0, 2).map((rule) => (
                          <div key={rule.id} className="text-xs bg-gray-50 p-2 rounded">
                            {metricOptions.find(m => m.value === rule.metric)?.label} {rule.operator} {rule.threshold}
                          </div>
                        ))}
                        {strategy.rules.length > 2 && (
                          <div className="text-xs text-gray-500">
                            +{strategy.rules.length - 2} more rules
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="text-xs text-gray-500 bg-gray-50 p-2 rounded">
                        {strategy.book
                          ? `Book (fund desk engine) — ${describeBookParams(strategy.book)}`
                          : strategy.wave
                          ? '🌊 Wave (tranche accumulate/exit) strategy'
                          : 'Filter strategy — compare / last-cross-direction conditions'}
                      </div>
                    )}
                    
                    <div className="flex items-center justify-between pt-3 border-t">
                      <span className="text-xs text-gray-500">
                        Modified: {strategy.lastModified}
                      </span>
                      <div className="flex items-center gap-2">
                        <Button 
                          size="sm" 
                          variant="outline"
                          onClick={() => editStrategy(strategy)}
                          className="flex items-center gap-1"
                        >
                          <Edit2 className="h-3 w-3" />
                          Edit
                        </Button>
                        <Button 
                          size="sm" 
                          variant="outline"
                          onClick={() => deleteStrategy(strategy.id)}
                          className="flex items-center gap-1 text-red-600 hover:text-red-700 hover:bg-red-50"
                        >
                          <Trash2 className="h-3 w-3" />
                          Delete
                        </Button>
                        <Button 
                          size="sm" 
                          onClick={() => startSimulation(strategy)}
                          className="flex items-center gap-1"
                        >
                          <Play className="h-3 w-3" />
                          Simulate
                        </Button>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}

        {/* Create Strategy Modal */}
        {showCreateStrategy && (
          <Card className="max-w-4xl mx-auto">
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span>{editingStrategy ? 'Edit Strategy' : 'Create New Strategy'}</span>
                <Button
                  variant="outline"
                  onClick={() => {
                    setShowCreateStrategy(false);
                    setEditingStrategy(null);
                    setNewStrategyName('');
                    setNewStrategyDescription('');
                    setNewStrategyRules([]);
                    resetWaveState();
                    resetBookState();
                  }}
                >
                  Cancel
                </Button>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Basic Info */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Strategy Name
                  </label>
                  <Input
                    value={newStrategyName}
                    onChange={(e) => setNewStrategyName(e.target.value)}
                    placeholder="e.g., High Momentum Strategy"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Description
                  </label>
                  <Input
                    value={newStrategyDescription}
                    onChange={(e) => setNewStrategyDescription(e.target.value)}
                    placeholder="Brief description of the strategy"
                  />
                </div>
              </div>

              {bookEnabled && (
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
                  ⚠ Book engine is on — the book defines selection and exits, so the filter conditions and
                  wave engine below are disabled and will not be saved. Turn the book off to use them.
                </p>
              )}

              {/* Filter conditions (stateless qualification) */}
              <div className={bookEnabled ? 'opacity-40 pointer-events-none' : ''}>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-lg font-medium text-gray-900">Filter conditions</h3>
                  {filterSignalCount() > 0 && (
                    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-700">
                      {filterSignalCount()} condition{filterSignalCount() !== 1 ? 's' : ''}
                    </span>
                  )}
                </div>
                <p className="text-xs text-gray-500 mb-3">
                  A stock qualifies when ANY group is true (signals AND within a group, groups OR across).
                  Use <span className="font-medium">Compare metrics</span> (metric vs a number or another metric)
                  and <span className="font-medium">Last cross direction</span>. Leave empty if this strategy
                  only uses the Wave engine below.
                </p>
                <SignalGroupEditor
                  groups={filterGroups}
                  setGroups={setFilterGroups}
                  label="Filter group"
                  allowedKinds={['compare', 'last_cross_dir']}
                />
              </div>

              {/* ── Stateful / Wave Builder ────────────────────────────────────── */}
              <div className={`border border-gray-200 rounded-lg ${bookEnabled ? 'opacity-40 pointer-events-none' : ''}`}>
                {/* Collapsible header */}
                <button
                  type="button"
                  onClick={() => setWaveOpen(o => !o)}
                  className="flex items-center justify-between w-full px-4 py-3 text-left hover:bg-gray-50 rounded-lg transition-colors"
                >
                  <div className="flex items-center gap-2">
                    <Activity className="h-4 w-4 text-indigo-600" />
                    <span className="text-sm font-medium text-gray-900">Stateful / Wave Engine</span>
                    {entryGroups.length > 0 && (
                      <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-indigo-100 text-indigo-700">
                        {entryGroups.reduce((s, g) => s + g.length, 0)} entry signal{entryGroups.reduce((s, g) => s + g.length, 0) !== 1 ? 's' : ''}
                      </span>
                    )}
                  </div>
                  {waveOpen
                    ? <ChevronDown className="h-4 w-4 text-gray-500" />
                    : <ChevronRight className="h-4 w-4 text-gray-500" />}
                </button>

                {waveOpen && (
                  <div className="px-4 pb-4 space-y-5 border-t border-gray-100 pt-4">
                    <p className="text-xs text-gray-500">
                      Optional — define entry/accumulate/exit signals for a tranche-based wave
                      strategy. Leave empty to use the standard rules-only engine.
                    </p>

                    {/* Tranche & exit settings. Capital is set at run time via the
                        run config's "Portfolio Base Value" — tranche size = capital / tranches. */}
                    <p className="text-xs text-gray-500 -mt-1">
                      Capital is set when you run (Portfolio Base Value). Tranche size = capital ÷ tranches.
                    </p>
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Tranches</label>
                        <input
                          type="number"
                          min={1}
                          max={100}
                          value={tranches}
                          onChange={e => setTranches(parseInt(e.target.value) || 20)}
                          className="w-full p-2 border border-gray-300 rounded-md text-sm"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Exit mode</label>
                        <select
                          value={exitMode}
                          onChange={e => setExitMode(e.target.value as 'full' | 'fractional')}
                          className="w-full p-2 border border-gray-300 rounded-md text-sm"
                        >
                          <option value="full">Full exit</option>
                          <option value="fractional">Partial (1 tranche)</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Re-entry</label>
                        <select
                          value={reentry ? 'yes' : 'no'}
                          onChange={e => setReentry(e.target.value === 'yes')}
                          className="w-full p-2 border border-gray-300 rounded-md text-sm"
                        >
                          <option value="yes">Allowed</option>
                          <option value="no">Blocked</option>
                        </select>
                      </div>
                    </div>

                    {/* Buy cadence */}
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Buy cadence</label>
                        <select
                          value={buyCadence}
                          onChange={e => setBuyCadence(e.target.value as 'daily' | 'weekly' | 'monthly')}
                          className="w-full p-2 border border-gray-300 rounded-md text-sm"
                        >
                          <option value="daily">Daily</option>
                          <option value="weekly">Weekly</option>
                          <option value="monthly">Monthly</option>
                        </select>
                      </div>
                      {buyCadence !== 'daily' && (
                        <div>
                          <label className="block text-xs font-medium text-gray-600 mb-1">Buy date</label>
                          <select
                            value={buyDate}
                            onChange={e => setBuyDate(e.target.value as 'first' | 'last')}
                            className="w-full p-2 border border-gray-300 rounded-md text-sm"
                          >
                            <option value="first">First of period</option>
                            <option value="last">Last of period</option>
                          </select>
                        </div>
                      )}
                    </div>

                    {/* Entry signals */}
                    <div className="border border-green-200 rounded-lg p-3 bg-green-50/30">
                      <SignalGroupEditor
                        groups={entryGroups}
                        setGroups={setEntryGroups}
                        label="Entry signals (required to open a tranche)"
                      />
                    </div>

                    {/* Accumulate signals */}
                    <div className="border border-blue-200 rounded-lg p-3 bg-blue-50/20">
                      <SignalGroupEditor
                        groups={accumGroups}
                        setGroups={setAccumGroups}
                        label="Accumulate signals (add tranches to open position)"
                      />
                    </div>

                    {/* Exit signals */}
                    <div className="border border-red-200 rounded-lg p-3 bg-red-50/20">
                      <SignalGroupEditor
                        groups={exitGroups}
                        setGroups={setExitGroups}
                        label="Exit signals (reduce or close position)"
                      />
                    </div>
                  </div>
                )}
              </div>

              {/* ── Book engine (fund desk) ────────────────────────────────────── */}
              <div className="border border-gray-200 rounded-lg">
                {/* Collapsible header */}
                <button
                  type="button"
                  onClick={() => setBookOpen(o => !o)}
                  className="flex items-center justify-between w-full px-4 py-3 text-left hover:bg-gray-50 rounded-lg transition-colors"
                >
                  <div className="flex items-center gap-2">
                    <Layers className="h-4 w-4 text-emerald-600" />
                    <span className="text-sm font-medium text-gray-900">Book Engine</span>
                    {bookEnabled && (
                      <span className="ml-2 inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-700">
                        {BOOK_KIND_LABELS[bookKind]}
                      </span>
                    )}
                  </div>
                  {bookOpen
                    ? <ChevronDown className="h-4 w-4 text-gray-500" />
                    : <ChevronRight className="h-4 w-4 text-gray-500" />}
                </button>

                {bookOpen && (
                  <div className="px-4 pb-4 space-y-4 border-t border-gray-100 pt-4">
                    <p className="text-xs text-gray-500">
                      Optional — run one of the fund desk&apos;s books on its own engine. A book strategy is
                      exclusive: it has no filter conditions or wave, and the book defines its schedule
                      (weekly), holdings and exits.
                    </p>
                    <label className="flex items-center gap-2 text-sm font-medium text-gray-700">
                      <input
                        type="checkbox"
                        checked={bookEnabled}
                        onChange={e => setBookEnabled(e.target.checked)}
                        className="h-4 w-4 rounded border-gray-300 text-emerald-600 focus:ring-emerald-500"
                      />
                      Use the book engine for this strategy
                    </label>

                    <div className={`space-y-3 ${bookEnabled ? '' : 'opacity-40 pointer-events-none'}`}>
                      <div>
                        <label className="block text-xs font-medium text-gray-600 mb-1">Book</label>
                        <select
                          value={bookKind}
                          disabled={!bookEnabled}
                          onChange={e => setBookKind(e.target.value as BookKind)}
                          className="w-full md:w-72 p-2 border border-gray-300 rounded-md text-sm"
                        >
                          <option value="grid10">{BOOK_KIND_LABELS.grid10}</option>
                          <option value="tranche">{BOOK_KIND_LABELS.tranche}</option>
                        </select>
                        <p className="text-xs text-gray-500 mt-1">{BOOK_KIND_DESCRIPTIONS[bookKind]}</p>
                      </div>

                      {bookKind === 'grid10' ? (
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                          <div>
                            <label className="block text-xs font-medium text-gray-600 mb-1">Names (1–50)</label>
                            <input
                              type="number"
                              min={1}
                              max={50}
                              value={bookN}
                              disabled={!bookEnabled}
                              onChange={e => setBookN(parseInt(e.target.value) || BOOK_DEFAULTS.n)}
                              className="w-full p-2 border border-gray-300 rounded-md text-sm"
                            />
                          </div>
                        </div>
                      ) : (
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                          <div>
                            <label className="block text-xs font-medium text-gray-600 mb-1">Top N per week (1–20)</label>
                            <input
                              type="number"
                              min={1}
                              max={20}
                              value={bookTop}
                              disabled={!bookEnabled}
                              onChange={e => setBookTop(parseInt(e.target.value) || BOOK_DEFAULTS.top)}
                              className="w-full p-2 border border-gray-300 rounded-md text-sm"
                            />
                          </div>
                          <div>
                            <label className="block text-xs font-medium text-gray-600 mb-1">Hold weeks (1–52)</label>
                            <input
                              type="number"
                              min={1}
                              max={52}
                              value={bookWeeks}
                              disabled={!bookEnabled}
                              onChange={e => setBookWeeks(parseInt(e.target.value) || BOOK_DEFAULTS.weeks)}
                              className="w-full p-2 border border-gray-300 rounded-md text-sm"
                            />
                          </div>
                          <div>
                            <label className="block text-xs font-medium text-gray-600 mb-1">Slot divisor (1–1000)</label>
                            <input
                              type="number"
                              min={1}
                              max={1000}
                              value={bookSlotDiv}
                              disabled={!bookEnabled}
                              onChange={e => setBookSlotDiv(parseInt(e.target.value) || BOOK_DEFAULTS.slot_div)}
                              className="w-full p-2 border border-gray-300 rounded-md text-sm"
                            />
                          </div>
                          <div>
                            <label className="block text-xs font-medium text-gray-600 mb-1">Per-name cap % (1–100)</label>
                            <input
                              type="number"
                              min={1}
                              max={100}
                              step={0.5}
                              value={bookMaxWPct}
                              disabled={!bookEnabled}
                              onChange={e => setBookMaxWPct(parseFloat(e.target.value) || BOOK_DEFAULTS.max_w * 100)}
                              className="w-full p-2 border border-gray-300 rounded-md text-sm"
                            />
                          </div>
                          <div className="flex items-end pb-2">
                            <label className="flex items-center gap-2 text-xs text-gray-700">
                              <input
                                type="checkbox"
                                checked={bookDeployCash}
                                disabled={!bookEnabled}
                                onChange={e => setBookDeployCash(e.target.checked)}
                                className="h-3 w-3 rounded border-gray-300"
                              />
                              Deploy idle cash
                            </label>
                          </div>
                        </div>
                      )}
                      {bookEnabled && bookParamError() && (
                        <p className="text-xs text-red-600">{bookParamError()}</p>
                      )}
                    </div>
                  </div>
                )}
              </div>

              {/* Save Button */}
              <div className="flex justify-end gap-3 pt-6 border-t">
                <Button
                  variant="outline"
                  onClick={() => {
                    setShowCreateStrategy(false);
                    resetWaveState();
                    resetBookState();
                  }}
                >
                  Cancel
                </Button>
                <Button
                  onClick={saveStrategy}
                  // Valid if it has a name AND defines either a valid book, filter
                  // conditions, OR a wave (>=1 entry signal).
                  disabled={!newStrategyName || (bookEnabled
                    ? bookParamError() !== null
                    : (filterSignalCount() === 0 && entryGroups.reduce((s, g) => s + g.length, 0) === 0))}
                  className="flex items-center gap-2"
                >
                  <Target className="h-4 w-4" />
                  {editingStrategy ? 'Update Strategy' : 'Save Strategy'}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Simulation Configuration Modal */}
        {showSimulationConfig && selectedStrategy && (
          <Card className="max-w-4xl mx-auto">
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span>Configure Simulation - {selectedStrategy.name}</span>
                <Button 
                  variant="outline" 
                  onClick={() => setShowSimulationConfig(false)}
                >
                  Cancel
                </Button>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Wave strategy notice — clarify which run-config fields apply */}
              {selectedStrategy.wave && (
                <div className="rounded-md border border-indigo-200 bg-indigo-50 p-3 text-sm text-indigo-900">
                  <div className="font-medium mb-1">🌊 Wave strategy</div>
                  <p>
                    Tranche count and buy timing (cadence: <span className="font-medium">{selectedStrategy.wave.buy_cadence || 'daily'}</span>)
                    come from the saved wave — edit the strategy to change them.
                    <strong> Portfolio Base Value</strong> is the capital (split into tranches),
                    and <strong>Universe, dates, Point-in-time</strong> apply.
                    <strong> Max Holdings</strong> caps concurrent positions and <strong>Momentum Ranking</strong> ranks
                    new entries when the cap is hit (set it to “None” for no cap).
                    <strong> Primary Filter does not apply</strong> to waves — leave it on “None”.
                    <strong> Rebalance Frequency/Date/Type do not apply</strong> to waves (the wave cadence governs timing).
                  </p>
                </div>
              )}
              {/* Book strategy notice — the book defines its own schedule and selection */}
              {selectedStrategy.book && (
                <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-900">
                  <div className="font-medium mb-1">
                    Book strategy — {BOOK_KIND_LABELS[selectedStrategy.book.kind] ?? selectedStrategy.book.kind}
                  </div>
                  <p>
                    Runs on the fund desk engine ({describeBookParams(selectedStrategy.book)}).
                    It rebalances <strong>weekly</strong> on the weekday you pick, is always
                    <strong> point-in-time</strong>, and starts no earlier than <strong>{BOOK_START_FLOOR}</strong>.
                    <strong> Portfolio Base Value, Universe, Benchmark and dates</strong> apply; holdings, ranking,
                    allocation, primary filter and gates are defined by the book.
                  </p>
                </div>
              )}
              {/* Portfolio Settings */}
              <div>
                <h3 className="text-lg font-medium text-gray-900 mb-4">Portfolio Settings</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Portfolio Base Value (₹)
                    </label>
                    <Input
                      type="number"
                      value={simulationConfig.portfolioBaseValue}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        portfolioBaseValue: parseInt(e.target.value) || 100000
                      })}
                      placeholder="100000"
                    />
                  </div>
                  
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Universe
                    </label>
                    <select
                      value={simulationConfig.universe}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        universe: e.target.value
                      })}
                      className="w-full p-2 border border-gray-300 rounded-md"
                    >
                      {universeOptions.map((option) => {
                        // Block universes without reconstitution data while PIT
                        // is on — they cannot run rebalance-aware and would 400.
                        const blocked = (simulationConfig.pointInTimeMembership || isBook) && option.pit_eligible === false;
                        return (
                          <option key={option.value} value={option.value} disabled={blocked}>
                            {option.label}{blocked ? ' — no PIT data' : ''}
                          </option>
                        );
                      })}
                    </select>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Benchmark Symbol
                    </label>
                    <select
                      value={simulationConfig.benchmarkSymbol || '50 EQL Wgt'}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        benchmarkSymbol: e.target.value
                      })}
                      className="w-full p-2 border border-gray-300 rounded-md"
                    >
                      {benchmarkOptions.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-gray-500 mt-1">
                      Benchmark for performance comparison. Default is Equal Weight Index.
                    </p>
                  </div>
                </div>
              </div>

              {/* Rebalancing Settings */}
              <div>
                <h3 className="text-lg font-medium text-gray-900 mb-4">Rebalancing Settings</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Rebalance Frequency
                    </label>
                    <select
                      value={isBook ? 'weekly' : simulationConfig.rebalanceFrequency}
                      disabled={isBook}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        rebalanceFrequency: e.target.value as SimulationConfig['rebalanceFrequency']
                      })}
                      className="w-full p-2 border border-gray-300 rounded-md"
                    >
                      <option value="monthly">Monthly</option>
                      <option value="weekly">Weekly</option>
                      <option value="quarterly">Quarterly</option>
                      <option value="dynamic">Dynamic (immediate)</option>
                    </select>
                    {isBook && (
                      <p className="text-xs text-gray-500 mt-1">Book strategies always rebalance weekly.</p>
                    )}
                  </div>

                  {isBook ? (
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-2">
                        Rebalance Weekday
                      </label>
                      <select
                        value={simulationConfig.bookWeekday}
                        onChange={(e) => setSimulationConfig({
                          ...simulationConfig,
                          bookWeekday: parseInt(e.target.value)
                        })}
                        className="w-full p-2 border border-gray-300 rounded-md"
                      >
                        {WEEKDAY_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                      </select>
                      <p className="text-xs text-gray-500 mt-1">
                        The session of each week the book rebalances on.
                      </p>
                    </div>
                  ) : (
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Rebalance Date
                    </label>
                    <select
                      value={simulationConfig.rebalanceDate}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        rebalanceDate: e.target.value as SimulationConfig['rebalanceDate']
                      })}
                      className="w-full p-2 border border-gray-300 rounded-md"
                    >
                      <option value="first">First available date</option>
                      <option value="mid">Mid period date</option>
                      <option value="last">Last available date</option>
                    </select>
                  </div>
                  )}
                </div>

                {/* Rebalance Type Setting */}
                {isBook ? (
                  <div className="mt-6">
                    <h4 className="text-base font-medium text-gray-900 mb-3">Allocation Method</h4>
                    {bookDefinedNote}
                  </div>
                ) : (
                <div className="mt-6">
                  <h4 className="text-base font-medium text-gray-900 mb-3">Allocation Method</h4>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Rebalance Type
                    </label>
                    <select
                      value={simulationConfig.rebalanceType}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        rebalanceType: e.target.value as SimulationConfig['rebalanceType']
                      })}
                      className="w-full p-2 border border-gray-300 rounded-md"
                    >
                      {rebalanceTypeOptions.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-gray-500 mt-1">
                      {rebalanceTypeOptions.find(o => o.value === simulationConfig.rebalanceType)?.description}
                    </p>
                  </div>
                </div>
                )}

                {/* Point-in-time membership */}
                <div className="mt-6">
                  <h4 className="text-base font-medium text-gray-900 mb-3">Universe Membership</h4>
                  <div className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      id="pointInTimeMembership"
                      checked={simulationConfig.pointInTimeMembership || isBook}
                      disabled={isBook}
                      onChange={(e) => {
                        const enabled = e.target.checked;
                        // When enabling PIT, snap off any universe that lacks
                        // reconstitution data — otherwise the run 400s. Prefer
                        // NIFTY50, else the first eligible universe.
                        if (enabled) {
                          const current = universeOptions.find(o => o.value === simulationConfig.universe);
                          if (current && current.pit_eligible === false) {
                            const fallback =
                              universeOptions.find(o => o.value === 'NIFTY50' && o.pit_eligible) ??
                              universeOptions.find(o => o.pit_eligible);
                            setSimulationConfig({
                              ...simulationConfig,
                              pointInTimeMembership: true,
                              universe: fallback ? fallback.value : simulationConfig.universe
                            });
                            return;
                          }
                        }
                        setSimulationConfig({
                          ...simulationConfig,
                          pointInTimeMembership: enabled
                        });
                      }}
                      className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    />
                    <label htmlFor="pointInTimeMembership" className="text-sm font-medium text-gray-700">
                      Point-in-time membership (rebalance-aware)
                    </label>
                  </div>
                  <p className="text-xs text-gray-500 mt-1 ml-7">
                    Use the index constituents that existed at each rebalance date instead of today&apos;s list —
                    removes survivorship/look-ahead bias. Requires loaded reconstitution data
                    ({universeOptions.filter(o => o.pit_eligible).length} of {universeOptions.length} universes qualify; others are disabled while this is on).
                    {isBook && ' Always on for book strategies.'}
                  </p>
                </div>

                {/* Regime gate */}
                {isBook ? (
                  <div className="mt-6">
                    <h4 className="text-base font-medium text-gray-900 mb-3">Regime Gate</h4>
                    {bookDefinedNote}
                  </div>
                ) : (
                <div className="mt-6">
                  <h4 className="text-base font-medium text-gray-900 mb-3">Regime Gate</h4>
                  <div className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      id="regimeGate"
                      checked={simulationConfig.regimeGate}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        regimeGate: e.target.checked
                      })}
                      className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    />
                    <label htmlFor="regimeGate" className="text-sm font-medium text-gray-700">
                      Trend gate: go to cash below the {simulationConfig.gateSma}d SMA
                    </label>
                  </div>
                  <p className="text-xs text-gray-500 mt-1 ml-7">
                    Sells the whole book (with charges) while the gate index closes below its SMA and
                    re-enters the day it recovers. Cuts crash drawdowns; costs some CAGR in whipsaws.
                    Long-only.
                  </p>
                  {simulationConfig.regimeGate && (
                    <div className="grid grid-cols-2 gap-4 mt-3 ml-7">
                      <div>
                        <label className="block text-xs font-medium text-gray-700 mb-1">Gate index</label>
                        <select
                          value={simulationConfig.gateSymbol}
                          onChange={(e) => setSimulationConfig({ ...simulationConfig, gateSymbol: e.target.value })}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500"
                        >
                          <option value="Nifty 500">Nifty 500</option>
                          <option value="Nifty 50">Nifty 50</option>
                          <option value="Nifty 100">Nifty 100</option>
                          <option value="Nifty Midcap 150">Nifty Midcap 150</option>
                          <option value="Nifty Smallcap 250">Nifty Smallcap 250</option>
                        </select>
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-700 mb-1">SMA (days)</label>
                        <input
                          type="number"
                          min={20}
                          max={500}
                          value={simulationConfig.gateSma}
                          onChange={(e) => setSimulationConfig({ ...simulationConfig, gateSma: parseInt(e.target.value) || 200 })}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-blue-500"
                        />
                      </div>
                    </div>
                  )}
                </div>
                )}
              </div>

              {/* Primary Filter — runs AFTER the strategy filter, BEFORE momentum ranking */}
              {isBook ? (
                <div>
                  <h3 className="text-lg font-medium text-gray-900 mb-1">Primary Filter</h3>
                  {bookDefinedNote}
                </div>
              ) : (
              <div>
                <h3 className="text-lg font-medium text-gray-900 mb-1">Primary Filter</h3>
                <p className="text-xs text-gray-500 mb-4">
                  Applied to everything the strategy filter passed — current holdings included —
                  before momentum ranking picks the top N.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Filter
                    </label>
                    <select
                      value={simulationConfig.primaryFilterTake}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        primaryFilterTake: e.target.value as SimulationConfig['primaryFilterTake']
                      })}
                      className="w-full p-2 border border-gray-300 rounded-md"
                    >
                      <option value="none">None — no primary filter</option>
                      <option value="all">All within the band</option>
                      <option value="top50">Top 50% of the band</option>
                      <option value="bottom50">Bottom 50% of the band</option>
                    </select>
                    <p className="text-xs text-gray-500 mt-1">
                      {simulationConfig.primaryFilterTake === 'none'
                        ? 'Stage skipped entirely'
                        : simulationConfig.primaryFilterTake === 'all'
                          ? 'Every name inside the band'
                          : simulationConfig.primaryFilterTake === 'top50'
                            ? (simulationConfig.primaryFilterMetric === 'ath'
                                ? 'The half nearest the high'
                                : 'The half nearest the low')
                            : (simulationConfig.primaryFilterMetric === 'ath'
                                ? 'The half furthest below the high'
                                : 'The half furthest above the low')}
                    </p>
                  </div>

                  <div className={simulationConfig.primaryFilterTake === 'none' ? 'opacity-40 pointer-events-none' : ''}>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Metric
                    </label>
                    <select
                      value={simulationConfig.primaryFilterMetric}
                      disabled={simulationConfig.primaryFilterTake === 'none'}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        primaryFilterMetric: e.target.value as SimulationConfig['primaryFilterMetric']
                      })}
                      className="w-full p-2 border border-gray-300 rounded-md"
                    >
                      <option value="ath">All Time High</option>
                      <option value="atl">All Time Low</option>
                    </select>
                    <p className="text-xs text-gray-500 mt-1">
                      {simulationConfig.primaryFilterMetric === 'ath'
                        ? 'Leaders: trading at or near their all-time-high close'
                        : 'Beaten-down: trading at or near their all-time-low close'}
                    </p>
                  </div>

                  <div className={simulationConfig.primaryFilterTake === 'none' ? 'opacity-40 pointer-events-none' : ''}>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Band (%)
                    </label>
                    <Input
                      type="number"
                      min="0"
                      max="100"
                      step="0.5"
                      value={simulationConfig.primaryFilterBand}
                      disabled={simulationConfig.primaryFilterTake === 'none'}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        primaryFilterBand: Math.max(0, parseFloat(e.target.value) || 0)
                      })}
                      className="w-full"
                    />
                    <p className="text-xs text-gray-500 mt-1">
                      {simulationConfig.primaryFilterMetric === 'ath'
                        ? `Keep names within ${simulationConfig.primaryFilterBand}% below their all-time high`
                        : `Keep names within ${simulationConfig.primaryFilterBand}% above their all-time low`}
                      {simulationConfig.primaryFilterBand === 0 && ' — 0% demands a fresh extreme'}
                    </p>
                  </div>
                </div>
                {simulationConfig.primaryFilterTake !== 'none' && selectedStrategy?.wave && (
                  <p className="mt-3 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded p-2">
                    ⚠ The primary filter is stateless-path only — a wave strategy will reject the run
                    rather than ignore it. Set Filter to “None” to run this wave.
                  </p>
                )}
              </div>
              )}

              {/* Portfolio Limit Settings */}
              {isBook ? (
                <div>
                  <h3 className="text-lg font-medium text-gray-900 mb-1">Portfolio Limit Settings</h3>
                  {bookDefinedNote}
                </div>
              ) : (
              <div>
                <h3 className="text-lg font-medium text-gray-900 mb-4">Portfolio Limit Settings</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className={simulationConfig.momentumRanking === 'none' ? 'opacity-40 pointer-events-none' : ''}>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Max Holdings
                    </label>
                    <Input
                      type="number"
                      min="1"
                      max="500"
                      value={simulationConfig.maxHoldings}
                      disabled={simulationConfig.momentumRanking === 'none'}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        maxHoldings: parseInt(e.target.value) || 10
                      })}
                      className="w-full"
                    />
                    <p className="text-xs text-gray-500 mt-1">
                      {simulationConfig.momentumRanking === 'none'
                        ? 'Not used — ranking is “None”, so every eligible name is held'
                        : 'Top N stocks ranked by momentum to hold'}
                    </p>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Momentum Ranking Method
                    </label>
                    <select
                      value={simulationConfig.momentumRanking}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        momentumRanking: e.target.value as SimulationConfig['momentumRanking']
                      })}
                      className="w-full p-2 border border-gray-300 rounded-md"
                    >
                      <option value="none">None — hold all eligible (no rank cut)</option>
                      <optgroup label="Price-Based Momentum">
                        <option value="20_day_return">20-Day Price Return</option>
                        <option value="price_roc_22d">22-Day Price Return</option>
                        <option value="price_roc_66d">66-Day Price Return</option>
                        <option value="price_roc_222d">222-Day Price Return</option>
                        <option value="risk_adjusted">Risk-Adjusted Return (Sharpe-like)</option>
                        <option value="technical">Technical Momentum (MA-based)</option>
                      </optgroup>
                      <optgroup label="Buy-the-Dip (lowest ROC first)">
                        <option value="price_roc_22d_low">Lowest 22-Day Return</option>
                        <option value="price_roc_222d_low">Lowest 222-Day Return</option>
                      </optgroup>
                      <optgroup label="Indicator-Based Momentum (22-day)">
                        <option value="truevx_roc">TrueVX Score ROC</option>
                        <option value="short_mean_roc">Short Mean ROC</option>
                        <option value="mid_mean_roc">Mid Mean ROC</option>
                        <option value="long_mean_roc">Long Mean ROC</option>
                        <option value="stock_score_roc">StockScore ROC</option>
                      </optgroup>
                    </select>
                    <p className="text-xs text-gray-500 mt-1">
                      {simulationConfig.momentumRanking === 'none'
                        ? 'No rank cut — every name passing the filters above is held'
                        : 'Method used to rank stocks when the portfolio limit is exceeded'}
                    </p>
                  </div>
                </div>
              </div>
              )}

              {/* Simulation Period */}
              <div>
                <h3 className="text-lg font-medium text-gray-900 mb-4">Simulation Period</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      Start Date
                    </label>
                    <Input
                      type="date"
                      min={isBook ? BOOK_START_FLOOR : undefined}
                      value={simulationConfig.startDate}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        startDate: e.target.value
                      })}
                    />
                    {isBook && (
                      <p className={`text-xs mt-1 ${bookStartTooEarly ? 'text-red-600' : 'text-gray-500'}`}>
                        Book strategies start on or after {BOOK_START_FLOOR} (validated point-in-time membership).
                      </p>
                    )}
                  </div>
                  
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-2">
                      End Date
                    </label>
                    <Input
                      type="date"
                      value={simulationConfig.endDate}
                      onChange={(e) => setSimulationConfig({
                        ...simulationConfig,
                        endDate: e.target.value
                      })}
                    />
                  </div>
                </div>
              </div>

              {/* Strategy Preview */}
              <div>
                <h3 className="text-lg font-medium text-gray-900 mb-4">Strategy Preview</h3>
                <div className="bg-gray-50 p-4 rounded-lg">
                  <h4 className="font-medium text-gray-800 mb-2">{selectedStrategy.name}</h4>
                  <p className="text-sm text-gray-600 mb-3">{selectedStrategy.description}</p>
                  {selectedStrategy.book && (
                    <div className="text-sm bg-white p-2 rounded border">
                      <span className="font-medium">
                        {BOOK_KIND_LABELS[selectedStrategy.book.kind] ?? selectedStrategy.book.kind}
                      </span>
                      <span className="mx-2 text-gray-400">·</span>
                      <span>{describeBookParams(selectedStrategy.book)}</span>
                    </div>
                  )}
                  <div className="space-y-1">
                    {selectedStrategy.rules.map((rule) => (
                      <div key={rule.id} className="text-sm bg-white p-2 rounded border">
                        <span className="font-medium">
                          {metricOptions.find(m => m.value === rule.metric)?.label}
                        </span>
                        <span className="mx-2">{rule.operator}</span>
                        <span className="font-medium">{rule.threshold}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              {/* Action Buttons */}
              <div className="flex justify-end gap-3 pt-6 border-t">
                <Button 
                  variant="outline" 
                  onClick={() => setShowSimulationConfig(false)}
                >
                  Cancel
                </Button>
                {/* A disabled button swallows hover (pointer-events-none), so the tooltip lives on a wrapper. */}
                <span title={isBook ? sweepDisabledTitle : undefined}>
                <Button 
                  onClick={() => {
                    // Navigate to time-period multi-run page
                    const params = {
                      strategyId: simulationConfig.strategyId,
                      portfolioValue: simulationConfig.portfolioBaseValue,
                      rebalanceFreq: simulationConfig.rebalanceFrequency,
                      rebalanceDate: simulationConfig.rebalanceDate,
                      rebalanceType: simulationConfig.rebalanceType,
                      universe: simulationConfig.universe,
                      benchmarkSymbol: simulationConfig.benchmarkSymbol || '50 EQL Wgt',
                      maxHoldings: simulationConfig.maxHoldings,
                      momentumRanking: simulationConfig.momentumRanking,
                      startDate: simulationConfig.startDate,
                      endDate: simulationConfig.endDate
                    };
                    const encodedParams = encodeURIComponent(JSON.stringify(params));
                    window.location.href = `/analytics/simulator/multi-run?params=${encodedParams}`;
                  }}
                  disabled={isBook}
                  variant="outline"
                  className="flex items-center gap-2"
                >
                  <Layers className="h-4 w-4" />
                  ML Simulation
                </Button>
                </span>
                <span title={isBook ? sweepDisabledTitle : undefined}>
                <Button 
                  onClick={() => {
                    // Navigate to holdings multi-dimension page
                    const params = {
                      strategyId: simulationConfig.strategyId,
                      portfolioValue: simulationConfig.portfolioBaseValue,
                      rebalanceFreq: simulationConfig.rebalanceFrequency,
                      rebalanceDate: simulationConfig.rebalanceDate,
                      rebalanceType: simulationConfig.rebalanceType,
                      universe: simulationConfig.universe,
                      benchmarkSymbol: simulationConfig.benchmarkSymbol || '50 EQL Wgt',
                      maxHoldings: simulationConfig.maxHoldings,
                      // This sweep varies the top-N, so "no rank cut" has nothing to
                      // sweep — the endpoint rejects it. Fall back to the default ranker.
                      momentumRanking: simulationConfig.momentumRanking === 'none'
                        ? '20_day_return'
                        : simulationConfig.momentumRanking,
                      startDate: simulationConfig.startDate,
                      endDate: simulationConfig.endDate
                    };
                    const encodedParams = encodeURIComponent(JSON.stringify(params));
                    window.location.href = `/analytics/simulator/holdings-multi-run?params=${encodedParams}`;
                  }}
                  disabled={isBook}
                  variant="outline"
                  className="flex items-center gap-2"
                >
                  <Target className="h-4 w-4" />
                  Multi-Dimension Holdings
                </Button>
                </span>
                <span title={bookStartTooEarly ? `Book strategies start on or after ${BOOK_START_FLOOR}` : undefined}>
                <Button
                  onClick={runSimulation}
                  disabled={bookStartTooEarly}
                  className="flex items-center gap-2"
                >
                  <Play className="h-4 w-4" />
                  Start Simulation
                </Button>
                </span>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Loading/Error States */}
        {isLoading && (
          <div className="text-center py-8">
            <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-500 mx-auto"></div>
            <p className="mt-2 text-gray-600">Loading...</p>
          </div>
        )}

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-red-800">
            {error}
          </div>
        )}

        {/* Delete Confirmation Dialog */}
        {showDeleteConfirm && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
            <Card className="w-full max-w-md mx-4">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-red-600">
                  <Trash2 className="h-5 w-5" />
                  Delete Strategy
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-gray-700 mb-6">
                  Are you sure you want to delete this strategy? This action cannot be undone.
                </p>
                <div className="flex gap-3 justify-end">
                  <Button 
                    variant="outline" 
                    onClick={cancelDelete}
                    disabled={isDeleting}
                  >
                    Cancel
                  </Button>
                  <Button 
                    onClick={confirmDeleteStrategy}
                    disabled={isDeleting}
                    className="bg-red-600 hover:bg-red-700 text-white"
                  >
                    {isDeleting ? (
                      <>
                        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white mr-2"></div>
                        Deleting...
                      </>
                    ) : (
                      <>
                        <Trash2 className="h-4 w-4 mr-2" />
                        Delete
                      </>
                    )}
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
};

export default AnalyticsSimulatorPage;
