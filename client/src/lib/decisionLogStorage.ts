interface ReasoningStep {
  label: string;
  detail: string;
}

interface TraceSpan {
  name: string;
  span_type: string;
  status: string;
  duration_ms: number;
  inputs?: string;
  outputs?: string;
}

export interface DecisionEntry {
  id: string;
  timestamp: string;
  query: string;
  specialist: string;
  recommendation: string;
  userAction: 'accepted' | 'rejected' | 'noted';
  reasoningSteps?: ReasoningStep[];
  responseTimeMs?: number;
  modelUsed?: string;
  traceSpans?: TraceSpan[];
}

const STORAGE_KEY = 'line_manager_decision_log';

export function loadLog(): DecisionEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    return JSON.parse(raw) as DecisionEntry[];
  } catch {
    return [];
  }
}

function saveLog(entries: DecisionEntry[]) {
  // why: keep storage bounded for long demo sessions while preserving recent context.
  localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(-50)));
}

export function addDecision(
  query: string,
  specialist: string,
  recommendation: string,
  userAction: 'accepted' | 'rejected' | 'noted' = 'noted',
  reasoningSteps?: ReasoningStep[],
  responseTimeMs?: number,
  modelUsed?: string,
  traceSpans?: TraceSpan[]
) {
  const entries = loadLog();
  entries.push({
    id: `DL-${Date.now()}`,
    timestamp: new Date().toISOString(),
    query,
    specialist,
    recommendation: recommendation.slice(0, 300),
    userAction,
    reasoningSteps,
    responseTimeMs,
    modelUsed,
    traceSpans,
  });
  saveLog(entries);
}
