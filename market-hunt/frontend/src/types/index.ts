// URL Management Types
export interface URLConfig {
  _id: string;
  url: string;
  index_name: string;
  description: string;
  tags: string[];
  is_active: boolean;
  is_valid: boolean;
  validation_message: string;
  created_at: Date;
  updated_at: Date;
  last_downloaded: Date | null;
  download_count: number;
  last_error: string | null;
}

export interface URLFormData {
  url: string;
  index_name?: string;
  description: string;
  tags: string[];
  is_active: boolean;
}

// Data Overview Types
export interface IndexStat {
  _id: string;
  count: number;
  last_update: Date;
}

export interface DataOverview {
  total_documents: number;
  index_stats: Array<{
    _id: string;
    count: number;
    last_update: string;
  }>;
}

// Index Data Types
export interface IndexConstituent {
  _id: string;
  'Company Name': string;
  'Industry': string;
  'Symbol': string;
  'Series': string;
  'ISIN Code': string;
  data_source: string;
  download_timestamp: Date;
  index_name: string;
}

// API Response Types
export interface APIResponse<T = unknown> {
  success: boolean;
  data?: T;
  message?: string;
  error?: string;
}

export interface ProcessingResponse {
  success: boolean;
  message: string;
  processed_count: number;
  total_count: number;
  results: Array<{
    url_id: string;
    index_name: string;
    success: boolean;
    documents_loaded?: number;
    message: string;
  }>;
}

// UI Component Types
export interface TableColumn<T = unknown> {
  id: string;
  header: string;
  accessorKey?: keyof T;
  cell?: (value: T) => React.ReactNode;
  sortable?: boolean;
}

export interface FilterOptions {
  search: string;
  status: 'all' | 'active' | 'inactive';
  tags: string[];
}

// Navigation Types
export interface NavItem {
  name: string;
  href: string;
  icon: React.ComponentType<React.SVGProps<SVGSVGElement>>;
  current: boolean;
}

// Chart Types
export interface ChartData {
  name: string;
  value: number;
}

export interface IndustryChart {
  industry: string;
  companies: number;
}

export interface ChartConfig {
  [key: string]: {
    label: string;
    color: string;
  };
}

// Stock Data Management Types
export interface SymbolMapping {
  symbol: string;
  company_name: string;
  industry: string;
  index_names: string[];
  nse_scrip_code: string | null;
  nse_symbol: string | null;
  nse_name: string | null;
  match_confidence: number | null;
  last_updated: string | null;
  is_up_to_date?: boolean | null;
  data_quality_score?: number | null;
}

export interface SymbolMappingFilters {
  index_name?: string;
  industry?: string;
  symbol_search?: string;
  mapped_only?: boolean;
}

export interface StockGapStatus {
  symbol: string;
  company_name: string;
  industry: string;
  index_names: string[];
  nse_scrip_code: string | null;
  has_data: boolean;
  record_count: number;
  date_range: {
    start: string | null;
    end: string | null;
  };
  data_freshness_days: number;
  coverage_percentage: number;
  last_price: number | null;
  needs_update: boolean;
  gap_details: string[];
}

export interface StockMappingsResponse {
  total_mappings: number;
  mapped_count: number;
  mappings: SymbolMapping[];
}

// Backward compatibility alias for StockMappingsResponse
export type SymbolMappingResponse = StockMappingsResponse;

export interface StockDownloadRequest {
  symbol: string;
  start_date?: string;
  end_date?: string;
  force_refresh?: boolean;
}

export interface StockDownloadResponse {
  success: boolean;
  message: string;
  task_id?: string;
  result?: {
    symbol: string;
    records_processed: number;
    operation_summary: {
      inserts: number;
      updates: number;
      skipped: number;
    };
    processing_time: number;
  };
}

export interface StockStatistics {
  total_symbols: number;
  symbols_with_data: number;
  total_price_records: number;
  date_range: {
    earliest: string;
    latest: string;
  };
  collections: {
    [key: string]: number;
  };
  storage_info: {
    total_size_mb: number;
    index_size_mb: number;
  };
}

// Stock price data (used by getStockData / StockChart)
export interface StockPriceData {
  date: string;
  open_price: number;
  high_price: number;
  low_price: number;
  close_price: number;
  volume: number;
}

export interface StockDataResponse {
  symbol?: string;
  // Backend record shape varies by endpoint; consumers narrow as needed.
  data: any[];
  total_records?: number;
}

export interface StockDataStatistics {
  total_records: number;
  unique_symbols_count: number;
  date_range: {
    earliest: string;
    latest: string;
  };
}

export interface DataGapInfo {
  symbol: string;
  missing_dates: string[];
  first_date: string;
  last_date: string;
  total_gaps: number;
  downloadable_gaps: number;
  realistic_start_date: string;
  user_range_start: string;
  user_range_end: string;
  user_range_gaps: number;
  gaps_by_year: Record<string, number>;
  data_available_from: string;
  data_available_until: string;
  total_data_points: number;
  full_period_gaps: boolean;
}

export interface DownloadStockDataRequest {
  symbol?: string;
  symbols?: string[];
  index_name?: string;
  industry?: string;
  start_date?: string;
  end_date?: string;
  sync_mode?: string;
}

export interface HistoricalProcessing {
  _id: string;
  task_id: string;
  request_type: 'symbol' | 'index' | 'industry';
  request_params: {
    symbol?: string;
    index_name?: string;
    industry?: string;
    start_date: string;
    end_date: string;
    force_refresh: boolean;
  };
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress_percentage: number;
  items_processed: number;
  total_items: number;
  started_at: Date;
  completed_at?: Date;
  error_message?: string;
  processed_symbols: string[];
  failed_symbols: string[];
}

export interface ProgressUpdate {
  task_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  progress_percentage: number;
  current_count: number;
  total_count: number;
  current_item?: string;
  started_at: Date;
  completed_at?: Date;
  message?: string;
  error?: string;
}

// Scheduler / process tracking types
export interface ProcessEntry {
  id: string;
  task_id?: string;
  type: string;
  symbol?: string;
  index_name?: string;
  industry?: string;
  status: string;
  items_processed: number;
  total_items: number;
  progress_percentage?: number;
  started_at?: string;
  completed_at?: string;
  created_at?: string;
  error_message?: string;
  summary?: Record<string, unknown>;
  processing_details?: any[];
}

export interface SchedulerData {
  pending: ProcessEntry[];
  running: ProcessEntry[];
  completed: ProcessEntry[];
}

// Raw progress payload returned by the scheduler's per-process progress endpoint.
// Different consumers read different (legacy) field names for the same data.
export interface TaskProgress {
  task_id: string;
  status: string;
  progress?: number;
  progress_percentage?: number;
  items_processed?: number;
  total_items?: number;
  current_count?: number;
  total_count?: number;
  current_item?: string;
  start_time?: string;
  estimated_completion?: string;
}

// Statistics Types
export interface URLStatistics {
  total_urls: number;
  active_urls: number;
  valid_urls: number;
  unique_indices: number;
  recent_downloads: Array<{
    url: string;
    index_name: string;
    last_downloaded: Date;
  }>;
}
