/**
 * Turns the raw roster from `/api/ops/models` into the shapes the Multi-AI
 * tab renders.
 *
 * The API answers "which endpoint serves which role", which is the right thing
 * for it to answer but the wrong way round for the page: the story is told by
 * vendor, and the headline claim ("six vendors, one API") has to count what is
 * *actually* answering here, not what the roster wishes for. A workspace that
 * substituted two roles onto a fallback tier is running fewer vendors than the
 * default roster names, and the page must say the smaller number.
 *
 * Kept out of the component and unit-tested because getting these counts subtly
 * wrong is how a demo ends up overstating what it is doing.
 */
import type { ModelRoster, RoleModel } from './useModelRoster';

export const ROLE_LABELS: Record<string, string> = {
  downtime_analyst: 'Downtime Analyst',
  quality_specialist: 'Quality Specialist',
  supply_chain_advisor: 'Supply Chain Advisor',
  oee_coach: 'OEE Coach',
  schedule_optimizer: 'Schedule Optimizer',
  maintenance_predictor: 'Maintenance Predictor',
  strategic_advisor: 'Strategic Advisor',
  shift_briefing: 'Shift Briefing',
  general: 'General / synthesis',
  intent_router: 'Intent router',
};

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

/**
 * The roster's rows, or none.
 *
 * The endpoint always sends `roles`, so this is defence against drift rather
 * than a case seen in the wild — but the cost of being wrong is out of
 * proportion to the check. Every consumer here maps over the array during
 * render, so one absent key throws inside React and takes the tab down; until
 * this release the Multi-AI and About tabs had no error boundary, which meant
 * it took the whole app with it.
 */
function roleRows(roster: ModelRoster | null): RoleModel[] {
  return Array.isArray(roster?.roles) ? roster.roles : [];
}

export interface VendorGroup {
  vendor: string;
  roles: RoleModel[];
  /** Distinct model names this vendor serves here, in roster order. */
  models: string[];
}

/**
 * Group roles by the vendor that actually answers, busiest vendor first.
 *
 * Ties break on first appearance in the roster rather than alphabetically, so
 * the ordering stays stable across reloads instead of shuffling when two
 * vendors hold the same number of seats.
 */
export function vendorBreakdown(roster: ModelRoster | null): VendorGroup[] {
  const order: string[] = [];
  const byVendor = new Map<string, RoleModel[]>();

  for (const role of roleRows(roster)) {
    const vendor = role.vendor || 'unknown';
    if (!byVendor.has(vendor)) {
      byVendor.set(vendor, []);
      order.push(vendor);
    }
    byVendor.get(vendor)!.push(role);
  }

  return order
    .map((vendor) => {
      const roles = byVendor.get(vendor)!;
      const models: string[] = [];
      for (const r of roles) if (!models.includes(r.display)) models.push(r.display);
      return { vendor, roles, models };
    })
    .sort((a, b) => b.roles.length - a.roles.length || order.indexOf(a.vendor) - order.indexOf(b.vendor));
}

export interface RosterStats {
  vendorCount: number;
  roleCount: number;
  substituted: number;
  /** True when every role runs on its preferred model. */
  fullRoster: boolean;
  multiVendor: boolean;
}

export function rosterStats(roster: ModelRoster | null): RosterStats {
  const roles = roleRows(roster);
  // Counted from resolved rows rather than roster.vendors so the number always
  // matches the table below it, even if the two ever drift.
  const vendors = new Set(roles.map((r) => r.vendor).filter((v) => v && v !== 'unknown'));
  return {
    vendorCount: vendors.size,
    roleCount: roles.length,
    substituted: roles.filter((r) => r.substituted).length,
    fullRoster: roles.length > 0 && roles.every((r) => !r.substituted),
    multiVendor: roster?.multi_vendor ?? false,
  };
}
