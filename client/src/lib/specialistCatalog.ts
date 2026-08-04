/**
 * The specialist roster as the UI describes it: name, icon, scope, tools.
 *
 * Lives outside any one tab because two views need the same list and they must
 * not drift — the System Map draws each specialist against the governed domain
 * it queries, and the About tab lists the tools each one carries. Models are
 * deliberately absent: those come from the server at runtime via the roster
 * endpoint, because the deployed model can differ from the preferred one.
 */

export interface Specialist {
  id: string;
  name: string;
  icon: string;
  /** Full scope prose, for the detailed About listing. */
  domain: string;
  /** Scope in a few words, for the System Map's leaf cards. */
  short: string;
  tools: string[];
}

/**
 * The deterministic solvers, and the short name each goes by in prose.
 *
 * One definition of what counts as an optimizer, so the System Map's
 * "2 optimizers" chip and the About tab's list can never disagree. The prose
 * names live here rather than in the copy because they had already drifted:
 * the map's card read "7 deterministic optimizers" above a sentence naming
 * six, having been written out by hand and never revisited when the seventh
 * arrived. On a page whose whole claim is that its numbers are real, a
 * reviewer who counts is the one you have to survive.
 */
export const OPTIMIZER_METHODS: Readonly<Record<string, string>> = {
  schedule_recovery: 'MILP recovery',
  stochastic_schedule_recovery: 'two-stage stochastic planning',
  multi_shift_plan: 'multi-shift sequencing',
  production_forecast: 'Monte Carlo forecast',
  quality_risk_assessment: 'Bayesian quality risk',
  oee_loss_analysis: 'Pareto loss ranking',
  estimate_rul: 'RUL trending',
};

export const OPTIMIZER_TOOLS: ReadonlySet<string> = new Set(Object.keys(OPTIMIZER_METHODS));

/** "MILP recovery, two-stage stochastic planning, …" — every one of them. */
export function optimizerMethodList(): string {
  return Object.values(OPTIMIZER_METHODS).join(', ');
}

/**
 * Counts by category, for the System Map's chips. Genie is excluded from both
 * counts: the governed space is not in `tools` at all (it is attached at build
 * time from whichever domains provisioned), and `genie_query` gets its own chip
 * rather than being buried in a total.
 */
export function toolMix(s: Specialist): { optimizers: number; functions: number } {
  let optimizers = 0;
  let functions = 0;
  for (const tool of s.tools) {
    if (tool === 'genie_query') continue;
    if (OPTIMIZER_TOOLS.has(tool)) optimizers += 1;
    else functions += 1;
  }
  return { optimizers, functions };
}

/**
 * True when the specialist also reaches the line ops Genie space — the curated
 * space over the live streaming tables — on top of whatever governed domain it
 * owns. Three do: the two that reason across a whole shift, and the briefing
 * that has no governed domain of its own.
 *
 * Called "line ops" rather than "live" because liveness is not what separates
 * these three: all eight read the live tables, with direct SQL. What these
 * three have is the ability to ask those tables in natural language.
 */
export function readsLineOps(s: Specialist): boolean {
  return s.tools.includes('genie_query');
}

export const SPECIALISTS: Specialist[] = [
  {
    id: 'downtime_analyst',
    name: 'Downtime Analyst',
    icon: '⏱️',
    domain: 'Unplanned stops, root cause analysis, cascade impact, recovery prioritization',
    short: 'Unplanned stops, root cause, cascade impact',
    tools: ['sql_query', 'calculator', 'detect_anomalies', 'load_plan_and_constraints', 'retrieve_similar_cases'],
  },
  {
    id: 'quality_specialist',
    name: 'Quality Specialist',
    icon: '🔬',
    domain: 'Quality deviations, SPC, reject analysis, quality holds, contamination risk',
    short: 'Deviations, SPC, holds, reject analysis',
    tools: [
      'sql_query',
      'calculator',
      'load_plan_and_constraints',
      'retrieve_similar_cases',
      'detect_anomalies',
      'quality_risk_assessment',
    ],
  },
  {
    id: 'supply_chain_advisor',
    name: 'Supply Chain Advisor',
    icon: '📦',
    domain: 'Inventory levels, material depletion, reorder timing, supplier coordination',
    short: 'Stock levels, depletion, reorder timing',
    tools: ['sql_query', 'calculator', 'load_plan_and_constraints', 'retrieve_similar_cases'],
  },
  {
    id: 'oee_coach',
    name: 'OEE Coach',
    icon: '📈',
    domain: 'OEE breakdown, loss categorization, efficiency improvement, benchmarking',
    short: 'OEE breakdown, loss Pareto, benchmarking',
    tools: [
      'sql_query',
      'genie_query',
      'calculator',
      'load_plan_and_constraints',
      'oee_loss_analysis',
      'production_forecast',
    ],
  },
  {
    id: 'schedule_optimizer',
    name: 'Schedule Optimizer',
    icon: '📅',
    domain: 'Recovery planning, speed optimization, overtime decisions, CIP scheduling, stochastic planning',
    short: 'Recovery plans, speed, overtime, CIP',
    tools: [
      'sql_query',
      'calculator',
      'load_plan_and_constraints',
      'schedule_recovery',
      'production_forecast',
      'stochastic_schedule_recovery',
    ],
  },
  {
    id: 'maintenance_predictor',
    name: 'Maintenance Predictor',
    icon: '🔧',
    domain: 'Equipment health, anomaly trending, remaining useful life, predictive maintenance, PM scheduling',
    short: 'Equipment health, RUL, PM scheduling',
    tools: [
      'sql_query',
      'calculator',
      'detect_anomalies',
      'load_plan_and_constraints',
      'retrieve_similar_cases',
      'estimate_rul',
    ],
  },
  {
    id: 'strategic_advisor',
    name: 'Strategic Advisor',
    icon: '🎯',
    domain: 'Cross-shift trends, multi-shift planning, capex/opex framing, improvement roadmaps, benchmarking',
    short: 'Cross-shift trends, multi-shift planning',
    tools: [
      'sql_query',
      'genie_query',
      'calculator',
      'load_plan_and_constraints',
      'retrieve_similar_cases',
      'multi_shift_plan',
    ],
  },
  {
    id: 'shift_briefing',
    name: 'Shift Briefing',
    icon: '📝',
    domain: 'Pre-shift huddle and post-shift handover readouts — Genie-friendly, mobile-first summarisation',
    short: 'Pre-shift huddle and handover readouts',
    tools: ['sql_query', 'genie_query', 'calculator', 'load_plan_and_constraints', 'retrieve_similar_cases'],
  },
];
