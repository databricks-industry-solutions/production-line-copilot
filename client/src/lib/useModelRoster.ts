/**
 * Fetches which model actually answers for each agent role in THIS deployment.
 *
 * The About tab used to hardcode model names, which was fine when every
 * specialist shared one endpoint and wrong the moment they stopped. It is also
 * deployment-specific: a workspace that doesn't serve one of the six vendors
 * falls back to its tier endpoint for that role, and the page has to say so
 * rather than advertise a vendor it never calls.
 */
import { useEffect, useState } from 'react';

import { fetchOpsJson } from './opsFetch';

export interface RoleModel {
  role: string;
  endpoint: string;
  display: string;
  vendor: string;
  rationale: string;
  preferred_endpoint: string;
  preferred_display: string;
  preferred_vendor: string;
  substituted: boolean;
}

export interface ModelRoster {
  multi_vendor: boolean;
  vendors: string[];
  substituted: number;
  roles: RoleModel[];
}

/** `enabled=false` mounts without fetching — see the note on `useTopology`. */
export function useModelRoster(enabled = true): { roster: ModelRoster | null; error: string | null } {
  const [roster, setRoster] = useState<ModelRoster | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void (async () => {
      try {
        const { data, error: err } = await fetchOpsJson<ModelRoster>('/api/ops/models');
        if (cancelled) return;
        if (err) {
          setError(err);
          return;
        }
        setRoster(data);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return { roster, error };
}
