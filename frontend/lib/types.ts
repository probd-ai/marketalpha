export interface StrategyRule { id: string; metric: string; operator: string; threshold: number; name: string; }
export interface Strategy { id: string; name: string; description: string; rules: StrategyRule[]; }
export interface Universe { value: string; label: string; stock_count: number; }

export interface Sleeve {
  id: string; label: string; strategy_id: string; weight: number; universe: string;
  rebalance_frequency: string; rebalance_date: string; rebalance_type: string;
  max_holdings: number; momentum_ranking: string; include_brokerage: boolean; exchange: string;
}
export interface Portfolio {
  id?: string; name: string; description: string; total_capital: number; benchmark_symbol: string;
  inception_date: string; point_in_time_membership: boolean; sleeves: Sleeve[];
  created_at?: string; last_modified?: string;
}

export interface CurvePoint { date: string; portfolio_value: number; benchmark_value: number; }
export interface Holding {
  symbol: string; sleeve: string; quantity: number; current_price: number;
  market_value: number; weight: number; pnl: number; pnl_percent: number;
}
export interface SleeveAttribution {
  id: string; label: string; weight: number; total_return: number; contribution: number;
  holdings_count: number; coverage: { total_ever_members: number; covered: number; missing: string[] } | null;
}
export interface RunSummary {
  total_value: number; total_pnl: number; total_return: number; benchmark_return: number;
  alpha: number; max_drawdown: number; sharpe_ratio: number;
}
export interface RunResult {
  portfolio: { id: string; name: string; as_of: string; inception_date: string | null; point_in_time: boolean };
  summary: RunSummary; curve: CurvePoint[]; current_book: Holding[]; sleeves: SleeveAttribution[];
  rebalance_actions: { next_due: string | null; add: { symbol: string; sleeve: string; target_weight: number | null }[]; exit: { symbol: string; sleeve: string }[] };
}
