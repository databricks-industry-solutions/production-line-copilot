/**
 * Wraps incident trigger/resolve/recover API calls behind a small React hook.
 * Used by `App` and incident-related panels so UI code stays fetch-agnostic.
 * Keeps one shared loading flag because controls are intentionally serialized in the dashboard.
 */
import { useState, useCallback } from 'react';

interface RecoverMachineResult {
  machine_id: string;
  resolved_command_ids: string[];
  count: number;
  status: 'resolving' | 'no_active_incident';
}

interface UseIncidentApiResult {
  triggerIncident: (params: {
    shift_id?: string;
    scenario_id: string;
    target_machine_id?: string;
    severity?: string;
  }) => Promise<unknown>;
  resolveIncident: (commandId: string) => Promise<unknown>;
  recoverMachine: (machineId: string) => Promise<RecoverMachineResult>;
  loading: boolean;
}

/**
 * Trigger and resolve incidents via the simulation API.
 * Active commands come from SSE (passed in from parent), not polled here.
 */
export function useIncidentApi(): UseIncidentApiResult {
  const [loading, setLoading] = useState(false);

  const triggerIncident = useCallback(
    async (params: { shift_id?: string; scenario_id: string; target_machine_id?: string; severity?: string }) => {
      setLoading(true);
      try {
        const res = await fetch('/api/sim/incident', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ shift_id: 'live', ...params }),
        });
        if (!res.ok) throw new Error(`Trigger failed: ${res.status}`);
        return (await res.json()) as unknown;
      } finally {
        setLoading(false);
      }
    },
    []
  );

  const resolveIncident = useCallback(async (commandId: string) => {
    setLoading(true);
    try {
      const res = await fetch('/api/sim/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command_id: commandId }),
      });
      if (!res.ok) throw new Error(`Resolve failed: ${res.status}`);
      return (await res.json()) as unknown;
    } finally {
      setLoading(false);
    }
  }, []);

  // Recover all active incidents on a machine — used by the EventFeed where
  // the user has machine_id but not command_id.
  const recoverMachine = useCallback(async (machineId: string): Promise<RecoverMachineResult> => {
    setLoading(true);
    try {
      const res = await fetch('/api/sim/recover_machine', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ machine_id: machineId }),
      });
      if (!res.ok) throw new Error(`Recover failed: ${res.status}`);
      return (await res.json()) as RecoverMachineResult;
    } finally {
      setLoading(false);
    }
  }, []);

  return { triggerIncident, resolveIncident, recoverMachine, loading };
}
