/**
 * Fetches `/api/ops/quality-metrics` — the quality-gate figures the Data
 * Integrity tab draws itself, in place of the AI/BI dashboard it used to embed.
 * Datasets degrade one at a time on the server, so `warnings` can be non-empty
 * on an otherwise successful response and the panel says which figure is
 * missing rather than showing a zero.
 */
import { useCallback, useEffect, useState } from 'react';

export type QualitySummary = {
  bronze_rows_24h: number | null;
  dropped_rows_24h: number | null;
  pass_rate: number | null;
  latest_gold_ts: string | null;
};

export type TableHealthRow = {
  table_name: string;
  /** The stream's name in the reconciliation panel beside this one. */
  label: string;
  bronze_rows: number;
  dropped_rows: number;
  pass_rate: number;
  latest_ts: string | null;
  lag_minutes: number | null;
};

export type ThroughputPoint = { hour: string; row_count: number };
export type StatusSlice = { status: string; machines: number };
export type RejectReasonRow = {
  bronze_table: string;
  label: string;
  rejected_reason: string;
  rejected_rows: number;
};
export type QcFailureRow = {
  ts: string | null;
  machine_id: string;
  parameter: string;
  value: number | null;
  spec_min: number | null;
  spec_max: number | null;
  result: string;
};

export type QualityMetricsPayload = {
  window_hours: number;
  summary: QualitySummary | null;
  tables: TableHealthRow[];
  throughput: ThroughputPoint[];
  status_distribution: StatusSlice[];
  rejects_by_reason: RejectReasonRow[];
  qc_failures: QcFailureRow[];
  warnings: string[];
  cached?: boolean;
  error?: string;
};

export type QualityMetricsState = {
  data: QualityMetricsPayload | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
};

export function useQualityMetrics(): QualityMetricsState {
  const [data, setData] = useState<QualityMetricsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/ops/quality-metrics');
      const json = (await res.json()) as QualityMetricsPayload;
      if (!res.ok || json.error) {
        // Same rule as reconciliation: a failed refresh must not erase figures
        // someone is in the middle of reading.
        setError(json.error ?? `HTTP ${res.status}`);
      } else {
        setData(json);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return { data, error, loading, reload: () => void load() };
}
