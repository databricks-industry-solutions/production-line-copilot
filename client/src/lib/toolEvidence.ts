/**
 * The evidence a specialist's tool calls left behind, for the chat's
 * "How this decision was made" expander.
 *
 * The trail deliberately shows what the tools DID — the query that ran, the
 * tables it touched, how many rows came back — and not what the model said it
 * was thinking. A query a line manager can re-run is checkable; a rationale is
 * not, and reasoning text is often unfaithful to the actual computation.
 *
 * Shape mirrors `agent_server/tool_evidence.py`.
 */

export interface ToolEvidence {
  sql?: string;
  tables?: string[];
  rows?: number;
  /** The tool hit its own row cap, so `rows` is a floor, not the result size. */
  rows_capped?: boolean;
  /** Set for governed_<domain>_query, naming the governed space that answered. */
  domain?: string;
  error?: string;
}

export function hasEvidence(evidence?: ToolEvidence): evidence is ToolEvidence {
  if (!evidence) return false;
  return Boolean(
    evidence.sql ||
      evidence.error ||
      evidence.domain ||
      evidence.rows !== undefined ||
      (evidence.tables && evidence.tables.length > 0)
  );
}

/**
 * Row count as a phrase, or null when the tool reported no count.
 *
 * A capped count reads "20+" rather than "20": both tools truncate, and
 * printing the cap as if it were the answer would overstate what we know.
 */
export function rowSummary(evidence?: ToolEvidence): string | null {
  const rows = evidence?.rows;
  if (rows === undefined || rows === null) return null;
  if (evidence?.rows_capped) return `${rows}+ rows`;
  if (rows === 0) return 'no rows';
  return rows === 1 ? '1 row' : `${rows} rows`;
}

/** Hover text explaining a capped count, or null when the count is exact. */
export function rowSummaryTitle(evidence?: ToolEvidence): string | null {
  if (!evidence?.rows_capped) return null;
  return `The tool returned its maximum of ${evidence.rows} rows, so the full result set may be larger.`;
}

/** "governed quality domain", for the chip naming which space answered. */
export function domainLabel(domain?: string): string | null {
  if (!domain) return null;
  return `governed ${domain} domain`;
}
