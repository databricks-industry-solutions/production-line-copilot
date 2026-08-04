/** Display helper for "which model answered this" (see components/ChatPanel.tsx). */

/**
 * Render the model attribution for an answer.
 *
 * Specialists run on models from six different vendors, so the vendor is the
 * interesting half — "Claude Sonnet 5" means little to someone who doesn't
 * recognise the name, "Claude Sonnet 5 · Anthropic" means the platform routed
 * this question to Anthropic. Both fields come from the answer event; older
 * cached messages and the decision log predate them, so every combination has
 * to degrade to something truthful rather than showing "undefined".
 */
export function modelLabel(modelUsed?: string, display?: string, vendor?: string): string {
  const name = display?.trim() || modelUsed?.trim();
  const house = vendor?.trim();
  if (!name) return 'unknown model';
  // The backend reports "unknown" for an endpoint it can't attribute; that is
  // not a vendor name and should not be printed as one.
  if (!house || house === 'unknown') return name;
  return `${name} · ${house}`;
}
