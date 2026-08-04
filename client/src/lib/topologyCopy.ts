/**
 * Prose that has to agree with the deployment it is describing.
 *
 * The About tab and the System Map both count the Genie spaces this deployment
 * actually has, one directly above the other. Both used to state the full
 * wiring as a constant, which is right where the feature was built and wrong
 * anywhere it is switched off — and running without the governed model is a
 * supported configuration, not a fault. Sharing the wording keeps the two from
 * contradicting each other on the same screen.
 */

/** Schemas the governed manufacturing model splits into, when it is installed. */
export const GOVERNED_DOMAINS = 4;

/** Pluralised, because the count is 1 in exactly the deployment nobody checks. */
export function genieSpacePhrase(n: number): string {
  return `${n} Genie space${n === 1 ? '' : 's'}`;
}

export function spaceCountLabel(n: number): string {
  return `${genieSpacePhrase(n)} wired in this deployment`;
}

/**
 * What AI/BI Genie is doing here, for the platform card on the About tab.
 *
 * That card read "across five curated spaces" as a constant, two hundred lines
 * above a paragraph that counts them from the topology endpoint — so in any
 * deployment without the governed model the same page stated both five and
 * one.
 */
export function genieSpaceRole(configured: number, hasLineOps: boolean): string {
  const total = configured + (hasLineOps ? 1 : 0);
  if (total === 0) {
    return 'Natural language data analyst — no space is wired in this deployment';
  }
  const parts: string[] = [];
  if (hasLineOps) parts.push('one over the live streaming tables');
  if (configured >= GOVERNED_DOMAINS) {
    parts.push('one per governed domain (asset, quality, inventory, production)');
  } else if (configured === 1) {
    parts.push('one governed domain');
  } else if (configured > 1) {
    parts.push(`${configured} of the ${GOVERNED_DOMAINS} governed domains`);
  }
  const joined = parts.length === 2 ? `${parts[0]}, plus ${parts[1]}` : parts[0];
  return `Natural language data analyst across ${genieSpacePhrase(total)}: ${joined}`;
}

/**
 * The System Map's closing paragraph, which is a claim about wiring rather than
 * about design and so cannot be a constant.
 *
 * "Each specialist reaches one governed domain" sat directly beneath eight
 * tiles that, in an environment without the governed model, all said otherwise.
 * Each variant still ends on the live-SQL path, because that is the part that
 * is true everywhere and the part that makes an unprovisioned map describe a
 * working app rather than a broken one.
 */
export function reachSummary(provisioned: number, total: number): string {
  const shared =
    'The two that reason across a whole shift also read line ops, and all eight read the live ' +
    'tables with direct SQL, because Genie is a round trip and the shift is not.';

  // Answers the question the map otherwise invites: if narrow scope is the
  // argument, why is the line ops space 14 tables — more than all four governed
  // domains together? Because the split follows grant boundaries, and the live
  // schema has none to follow.
  const asymmetry =
    'Line ops is broader on purpose: the governed model splits along its own schemas because ' +
    'that is where grants live, while the live tables are a single schema behind a free-form ' +
    'surface, with no boundary to split on.';

  if (provisioned === 0) {
    return (
      'The governed manufacturing model is not installed in this deployment, so its domain ' +
      'Genie spaces are absent rather than empty and every specialist reaches its data with ' +
      `direct SQL. ${shared}`
    );
  }
  if (provisioned < total) {
    return (
      `${provisioned} of the ${total} governed domains are wired here — each is one schema of ` +
      'the manufacturing model, curated and permissioned separately, and narrow scope is what ' +
      'keeps Genie accurate. Specialists whose domain is absent fall back to SQL. ' +
      `${asymmetry} ${shared}`
    );
  }
  return (
    'Each specialist reaches one governed domain of the manufacturing model — its own schema, ' +
    'curated and permissioned separately, because narrow scope is what keeps Genie accurate. ' +
    `${asymmetry} ${shared}`
  );
}
