/**
 * Fetches `/api/ops/reconciliation` — the cross-path agreement check.
 * Used by the Data Integrity tab, which renders the verdict and the per-stream
 * detail in separate places on the page from a single fetch.
 */
import { useCallback, useEffect, useState } from 'react';

export type EntityRow = {
  key: string;
  label: string;
  lakebase_rows: number | null;
  bronze_rows: number | null;
  gold_rows: number | null;
  explained_rejected: number;
  ingest_delta: number | null;
  qc_delta: number | null;
  unexplained: number;
  status: 'ok' | 'warn' | 'error' | 'unknown' | 'idle';
  dqx_flagged: number;
};

export type GovernedRow = {
  key: string;
  label: string;
  schema: string;
  table: string;
  kind: 'upsert' | 'append';
  incident_driven: boolean;
  rows: number | null;
  last_ts: string | null;
  status: 'populated' | 'empty' | 'expected_empty' | 'absent';
};

export type OeeCheck = {
  live: number | null;
  governed: number | null;
  delta: number | null;
  status: 'ok' | 'warn' | 'error' | 'unknown' | 'not_enabled';
  note?: string | null;
};

export type ReconciliationPayload = {
  window: {
    minutes: number;
    lag_guard_seconds: number;
    /** Absolute bounds, identical for both engines. */
    start: string;
    end: string;
  };
  shift_id: string | null;
  /** True when served from the backend's short TTL cache. */
  cached?: boolean;
  entities: EntityRow[];
  governed: GovernedRow[];
  /**
   * False when the governed tables live in a catalog this environment reads but
   * does not write, so its rows belong to another deployment and are withheld.
   */
  governed_owned?: boolean;
  oee_check: OeeCheck;
  dqx_total: number;
  total_unexplained: number;
  overall: 'ok' | 'warn' | 'error' | 'unknown' | 'idle';
  live_path_available: boolean;
  warnings: string[];
  error?: string;
};

export type ReconciliationState = {
  data: ReconciliationPayload | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
};

export function useReconciliation(): ReconciliationState {
  const [data, setData] = useState<ReconciliationPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/ops/reconciliation');
      const json = (await res.json()) as ReconciliationPayload;
      if (!res.ok || json.error) {
        // Keep any previously loaded figures on screen; a failed refresh
        // shouldn't erase a reading the user was in the middle of reading.
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
