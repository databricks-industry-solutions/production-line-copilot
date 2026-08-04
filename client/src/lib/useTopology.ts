/**
 * Fetches which governed domains are actually provisioned in THIS deployment.
 *
 * The System Map draws a connection from a specialist to its governed Genie
 * space, and that connection is deployment-specific: a workspace where a
 * domain's space failed to create still runs, with those specialists falling
 * back to `sql_query`. Drawing the line anyway would make the diagram a claim
 * rather than a description, so the page asks the server what is really wired.
 */
import { useEffect, useState } from 'react';

import { fetchOpsJson } from './opsFetch';

export interface GovernedDomain {
  domain: string;
  label: string;
  configured: boolean;
  tool: string | null;
  entities: string[];
  specialists: string[];
}

export interface Topology {
  domains: GovernedDomain[];
  configured: number;
  total: number;
  streaming_space: boolean;
  mvm_silver_enabled: boolean;
}

/**
 * `enabled=false` keeps the hook mounted but makes no request — for a component
 * whose parent already holds this data and passes it down. Skipping the call
 * has to happen inside the effect, since skipping the hook itself is not
 * allowed.
 */
export function useTopology(enabled = true): { topology: Topology | null; error: string | null } {
  const [topology, setTopology] = useState<Topology | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    void (async () => {
      try {
        const { data, error: err } = await fetchOpsJson<Topology>('/api/ops/topology');
        if (cancelled) return;
        if (err) {
          setError(err);
          return;
        }
        setTopology(data);
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return { topology, error };
}
