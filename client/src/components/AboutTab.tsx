/**
 * Renders the architecture explainer tab for specialists, tools, platform, and pipeline tiers.
 * Used from `App` as the non-operational context page for demos and technical reviewers.
 * Text intentionally documents the tactical dual-write demo path and the preferred Synced Tables production pattern.
 */
import type { ReactNode } from 'react';

const SPECIALISTS = [
  {
    id: 'downtime_analyst',
    name: 'Downtime Analyst',
    icon: '⏱️',
    domain: 'Unplanned stops, root cause analysis, cascade impact, recovery prioritization',
    tools: ['sql_query', 'calculator', 'detect_anomalies', 'load_plan_and_constraints', 'retrieve_similar_cases'],
    model: 'GPT-5-mini',
  },
  {
    id: 'quality_specialist',
    name: 'Quality Specialist',
    icon: '🔬',
    domain: 'Quality deviations, SPC, reject analysis, quality holds, contamination risk',
    tools: [
      'sql_query',
      'calculator',
      'load_plan_and_constraints',
      'retrieve_similar_cases',
      'detect_anomalies',
      'quality_risk_assessment',
    ],
    model: 'GPT-5-mini',
  },
  {
    id: 'supply_chain_advisor',
    name: 'Supply Chain Advisor',
    icon: '📦',
    domain: 'Inventory levels, material depletion, reorder timing, supplier coordination',
    tools: ['sql_query', 'calculator', 'load_plan_and_constraints', 'retrieve_similar_cases'],
    model: 'GPT-5-mini',
  },
  {
    id: 'oee_coach',
    name: 'OEE Coach',
    icon: '📈',
    domain: 'OEE breakdown, loss categorization, efficiency improvement, benchmarking',
    tools: [
      'sql_query',
      'genie_query',
      'calculator',
      'load_plan_and_constraints',
      'oee_loss_analysis',
      'production_forecast',
    ],
    model: 'GPT-5',
  },
  {
    id: 'schedule_optimizer',
    name: 'Schedule Optimizer',
    icon: '📅',
    domain: 'Recovery planning, speed optimization, overtime decisions, CIP scheduling, stochastic planning',
    tools: [
      'sql_query',
      'calculator',
      'load_plan_and_constraints',
      'schedule_recovery',
      'production_forecast',
      'stochastic_schedule_recovery',
    ],
    model: 'GPT-5-mini',
  },
  {
    id: 'maintenance_predictor',
    name: 'Maintenance Predictor',
    icon: '🔧',
    domain: 'Equipment health, anomaly trending, remaining useful life, predictive maintenance, PM scheduling',
    tools: [
      'sql_query',
      'calculator',
      'detect_anomalies',
      'load_plan_and_constraints',
      'retrieve_similar_cases',
      'estimate_rul',
    ],
    model: 'GPT-5-mini',
  },
  {
    id: 'strategic_advisor',
    name: 'Strategic Advisor',
    icon: '🎯',
    domain: 'Cross-shift trends, multi-shift planning, capex/opex framing, improvement roadmaps, benchmarking',
    tools: [
      'sql_query',
      'genie_query',
      'calculator',
      'load_plan_and_constraints',
      'retrieve_similar_cases',
      'multi_shift_plan',
    ],
    model: 'GPT-5',
  },
  {
    id: 'shift_briefing',
    name: 'Shift Briefing',
    icon: '📝',
    domain: 'Pre-shift huddle and post-shift handover readouts — Genie-friendly, mobile-first summarisation',
    tools: ['sql_query', 'genie_query', 'calculator', 'load_plan_and_constraints', 'retrieve_similar_cases'],
    model: 'GPT-5-mini',
  },
];

const TOOLS: { id: string; name: string; icon: string; desc: string; platform?: string }[] = [
  {
    id: 'sql_query',
    name: 'SQL Query',
    icon: '🔷',
    desc: 'Read-only queries against Unity Catalog tables (auto-limited, injection-safe)',
    platform: 'Databricks SQL',
  },
  {
    id: 'genie_query',
    name: 'Genie Query',
    icon: '✨',
    desc: 'Natural language data analyst — translates questions to SQL against curated, governed datasets',
    platform: 'AI/BI Genie',
  },
  {
    id: 'calculator',
    name: 'Calculator',
    icon: '🧮',
    desc: 'OEE computation, recovery estimates, depletion forecasts, cost calculations',
  },
  {
    id: 'detect_anomalies',
    name: 'Anomaly Detector',
    icon: '📡',
    desc: 'Z-score and IQR analysis on raw sensor telemetry',
    platform: 'Databricks SQL',
  },
  {
    id: 'load_plan_and_constraints',
    name: 'Plan & Constraints',
    icon: '📋',
    desc: 'Loads production schedule, line speed limits, and CIP rules',
    platform: 'Unity Catalog',
  },
  {
    id: 'retrieve_similar_cases',
    name: 'Similar Cases',
    icon: '🔍',
    desc: 'Vector Search over historical events; falls back to SQL if unavailable',
    platform: 'Mosaic AI Vector Search',
  },
];

// Optimizers run as in-process Python tools inside the agent (LangChain
// @tool decorators in agent_server/optimizers/*.py). They are NOT served
// via Mosaic AI Model Serving — keeping the platform tag honest avoids
// the audit reviewers (and us) pointing at a serving endpoint that does
// not exist.
const OPTIMIZERS: { id: string; name: string; icon: string; method: string; desc: string; platform?: string }[] = [
  {
    id: 'schedule_recovery',
    name: 'Schedule Recovery',
    icon: '⚙️',
    method: 'Multi-objective MILP (OR-Tools SCIP)',
    desc: 'Optimises speed, overtime, quality risk, and energy cost. Auto-loads constraints from line_constraints table.',
  },
  {
    id: 'stochastic_schedule_recovery',
    name: 'Stochastic Schedule',
    icon: '🎰',
    method: 'Two-stage SAA (MILP + Monte Carlo)',
    desc: 'Robust speed/overtime decisions under uncertainty. 50 disruption scenarios evaluated inside the solver.',
  },
  {
    id: 'multi_shift_plan',
    name: 'Multi-Shift Planner',
    icon: '🗓️',
    method: 'Sequential Optimization',
    desc: 'Coordinates speed, overtime, CIP, and maintenance across multiple shifts with adjacency constraints.',
  },
  {
    id: 'production_forecast',
    name: 'Production Forecast',
    icon: '🎲',
    method: 'Monte Carlo (1,000 runs)',
    desc: 'Data-calibrated probabilistic forecast. MTTR, OEE variance, and ideal rate auto-loaded from historical data.',
  },
  {
    id: 'quality_risk_assessment',
    name: 'Quality Risk',
    icon: '🧪',
    method: 'Bayesian CPT (data-learned)',
    desc: 'CPT values learned from quality_checks + event_log history. Falls back to expert priors when data insufficient.',
  },
  {
    id: 'oee_loss_analysis',
    name: 'OEE Loss Analysis',
    icon: '📊',
    method: 'Pareto Ranking',
    desc: 'Ranks availability, performance, and quality losses by contribution and ROI estimate.',
  },
  {
    id: 'estimate_rul',
    name: 'RUL Estimator',
    icon: '⏳',
    method: 'Linear Trend Extrapolation',
    desc: 'Remaining useful life from sensor trends. Optimal maintenance via planned-vs-unplanned cost trade-off.',
  },
];

const PLATFORM = [
  { name: 'Databricks Apps', role: 'Application hosting (React + FastAPI)', icon: 'apps' },
  { name: 'Databricks SQL', role: 'Serverless query engine for dashboards and agents', icon: 'sql' },
  { name: 'AI/BI Genie', role: 'Natural language data analyst with curated Genie Space', icon: 'genie' },
  { name: 'Delta Lake', role: 'Bronze + Gold tables backing both the live and analytics paths', icon: 'delta' },
  { name: 'Unity Catalog', role: 'Governance, lineage, access control, model registry', icon: 'uc' },
  {
    name: 'Zerobus',
    role: 'Tier-2 gRPC Bronze ingest from the producer (analytics path; ~5-30 s materialization SLA)',
    icon: 'zerobus',
  },
  {
    name: 'Spark Structured Streaming',
    role: 'Continuous quality-gate job (streaming_quality_sync): validates Bronze, writes Gold + Lakebase, records rejects to quality_rejects',
    icon: 'lakeflow',
  },
  { name: 'Mosaic AI Model Serving', role: 'LLM inference (GPT-5-mini routing, GPT-5 strategy)', icon: 'serving' },
  { name: 'Mosaic AI Vector Search', role: 'Similar case retrieval for historical event matching', icon: 'vector' },
  {
    name: 'Lakebase',
    role: 'Tier-1 serving (ai_serving.*_history, sub-second producer dual-write) + agent memory',
    icon: 'lakebase',
  },
  { name: 'MLflow', role: 'Agent tracing, evaluation, experiment tracking', icon: 'mlflow' },
  { name: 'Asset Bundles', role: 'CI/CD deployment, infrastructure as code', icon: 'dabs' },
];

const PLATFORM_COLORS: Record<string, string> = {
  apps: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
  sql: 'bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300',
  genie: 'bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-300',
  delta: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
  uc: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300',
  lakeflow: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300',
  zerobus: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
  serving: 'bg-fuchsia-100 text-fuchsia-700 dark:bg-fuchsia-900/30 dark:text-fuchsia-300',
  vector: 'bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-300',
  lakebase: 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300',
  mlflow: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  dabs: 'bg-slate-100 text-slate-700 dark:bg-slate-800/50 dark:text-slate-300',
};

function DatabricksIcon({ className = 'w-4 h-4' }: { className?: string }) {
  return (
    <svg viewBox="0 0 36 36" className={className} fill="currentColor">
      <path d="M18 0L3.5 8.4v7.2L18 7.2l14.5 8.4V8.4L18 0z" opacity=".7" />
      <path d="M18 10.8L3.5 19.2v7.2L18 18l14.5 8.4v-7.2L18 10.8z" />
      <path d="M3.5 26.4L18 34.8l14.5-8.4v-7.2L18 27.6 3.5 19.2v7.2z" opacity=".7" />
    </svg>
  );
}

function PlatformTag({ name }: { name: string }) {
  return (
    <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
      <DatabricksIcon className="h-2.5 w-2.5" />
      {name}
    </span>
  );
}

function ToolBadge({ id, isOptimizer }: { id: string; isOptimizer?: boolean }) {
  const item = isOptimizer ? OPTIMIZERS.find((o) => o.id === id) : TOOLS.find((t) => t.id === id);
  if (!item) return null;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${
        isOptimizer
          ? 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300'
          : 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300'
      }`}
      title={item.desc}
    >
      {item.icon} {item.name}
    </span>
  );
}

export default function AboutTab() {
  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="text-center">
        <h2 className="text-xl font-bold text-foreground">ProdLine CoPilot</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          A compound AI system for brewery line management — 8 specialist agents, 6 tools (incl. Genie), 7 mathematical
          optimizers, built on the Databricks Data Intelligence Platform.
        </p>
      </div>

      {/* Agent Architecture */}
      <section>
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
          Agent Architecture
        </h3>
        {/* Orchestrator */}
        <div className="mb-4 rounded-lg border border-primary/30 bg-primary/5 p-4">
          <div className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary text-lg text-primary-foreground">
              🧠
            </span>
            <div>
              <div className="font-semibold text-foreground">Orchestrator</div>
              <div className="text-xs text-muted-foreground">
                Classifies user intent and routes to the right specialist. Builds context from 12 parallel SQL queries.
                Streams a Quick Snapshot instantly while the full analysis runs.
              </div>
            </div>
            <span className="ml-auto rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
              LangGraph
            </span>
          </div>
        </div>

        {/* Specialists grid */}
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {SPECIALISTS.map((s) => {
            const optimizerTools = s.tools.filter((t) => OPTIMIZERS.some((o) => o.id === t));
            const regularTools = s.tools.filter((t) => !OPTIMIZERS.some((o) => o.id === t));
            return (
              <div key={s.id} className="rounded-lg border bg-card p-4 shadow-sm">
                <div className="mb-2 flex items-start gap-2">
                  <span className="flex h-8 w-8 items-center justify-center rounded-md bg-muted text-base">
                    {s.icon}
                  </span>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-foreground">{s.name}</div>
                    <div className="text-[11px] text-muted-foreground">{s.model}</div>
                  </div>
                </div>
                <p className="mb-3 text-xs leading-relaxed text-muted-foreground">{s.domain}</p>
                <div className="flex flex-wrap gap-1">
                  {regularTools.map((t) => (
                    <ToolBadge key={t} id={t} />
                  ))}
                  {optimizerTools.map((t) => (
                    <ToolBadge key={t} id={t} isOptimizer />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Tools & Optimizers */}
      <div className="grid gap-6 md:grid-cols-2">
        <section>
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted-foreground">Tools</h3>
          <div className="space-y-2">
            {TOOLS.map((t) => (
              <div key={t.id} className="flex items-start gap-3 rounded-md border bg-card p-3">
                <span className="mt-0.5 text-base">{t.icon}</span>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-foreground">{t.name}</span>
                    {t.platform && <PlatformTag name={t.platform} />}
                  </div>
                  <div className="text-xs text-muted-foreground">{t.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
        <section>
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted-foreground">Optimizers</h3>
          <div className="space-y-2">
            {OPTIMIZERS.map((o) => (
              <div key={o.id} className="flex items-start gap-3 rounded-md border bg-card p-3">
                <span className="mt-0.5 text-base">{o.icon}</span>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-foreground">{o.name}</span>
                    {o.platform && <PlatformTag name={o.platform} />}
                  </div>
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-amber-600 dark:text-amber-400">
                    {o.method}
                  </div>
                  <div className="text-xs text-muted-foreground">{o.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* Databricks Platform */}
      <section>
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
          Databricks Data Intelligence Platform
        </h3>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {PLATFORM.map((p) => {
            const colors = PLATFORM_COLORS[p.icon] ?? 'bg-muted text-muted-foreground';
            return (
              <div key={p.name} className="flex items-start gap-3 rounded-md border bg-card p-3">
                <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${colors}`}>
                  <DatabricksIcon className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <div className="text-xs font-semibold text-foreground">{p.name}</div>
                  <div className="mt-0.5 text-[11px] text-muted-foreground">{p.role}</div>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Data Pipeline */}
      <section>
        <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted-foreground">Data Pipeline</h3>
        <div className="space-y-3 rounded-lg border bg-card p-4 text-xs">
          {/*
            Architecture: producer dual-writes at the source (LinkedIn /
            Uber pattern for hybrid OLTP+OLAP). Two sinks, two technologies
            chosen for each path's job:

              * Lakebase (psycopg2 batch insert)  → live UI, ~200-500 ms
              * Zerobus → Bronze Delta            → analytics, ~5-30 s
                                                    (server-side
                                                    materialization SLA)

            Tier-1 lives independent of the Tier-2 streaming consumer's
            health. If Lakebase blips, live UI degrades to current-state
            cache (_latest fed by Tier-2); analytics keeps running. If
            Tier-2 blips, live UI is unaffected; analytics catches up
            from bronze on resume.
          */}
          <div>
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Tier-1 serving (sub-second live UI)
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Pill color="blue">OT/IT Sources</Pill>
              <Arrow />
              <Pill color="orange">Producer (psycopg2 batch insert)</Pill>
              <Arrow />
              <Pill color="teal">Lakebase ai_serving.*_history</Pill>
              <Arrow />
              <Pill color="violet">SSE</Pill>
              <Arrow />
              <Pill color="red">React UI</Pill>
            </div>
          </div>
          {/*
            Tier-2 analytics: same producer also writes to Zerobus per
            tick. Bronze materializes at Zerobus' SLA (~5-30 s),
            Structured Streaming consumer (1 s processingTime trigger)
            applies QC + writes Gold + _latest cache. NOT Real-Time Mode:
            RTM only supports Kafka / Kinesis / Pulsar sources, not
            Delta — see footnote below.
          */}
          <div>
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              Tier-2 analytics + governed lakehouse
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Pill color="orange">Producer (Zerobus client)</Pill>
              <Arrow />
              <Pill color="amber">Zerobus</Pill>
              <Arrow />
              <Pill color="amber">Bronze Delta</Pill>
              <Arrow />
              <Pill color="orange">Structured Streaming (1s trigger)</Pill>
              <Arrow />
              <Pill color="emerald">Gold Delta</Pill>
              <Arrow />
              <Pill color="violet">SQL Warehouse</Pill>
              <Arrow />
              <Pill color="sky">Genie Space</Pill>
              <Arrow />
              <Pill color="red">AI agents</Pill>
            </div>
          </div>
          {/*
            Notes — important to call out so the diagram isn't misread.
          */}
          <div className="mt-3 space-y-1.5 rounded border-l-2 border-muted bg-muted/30 px-3 py-2 text-[11px] leading-relaxed text-muted-foreground">
            <p>
              <span className="font-medium text-foreground">Why dual-write at source.</span> Zerobus&apos; bronze
              materialization SLA is ~5-30 s (server-side, untunable). Routing the live UI through bronze inherits that
              floor. Producer →&nbsp;Lakebase direct keeps the UI sub-second; producer →&nbsp;Zerobus keeps the
              analytics path governed. Idempotent INSERTs (<code>ON CONFLICT DO NOTHING</code>) make the dual write safe
              under retry.
            </p>
            <p>
              <span className="font-medium text-foreground">Why not Real-Time Mode.</span> Spark Structured Streaming
              RTM (<code>Trigger.RealTime</code>) supports Kafka, Kinesis, and Pulsar sources — not Delta. With our
              Delta-sourced consumer,
              <code> processingTime(&quot;1 second&quot;) </code>
              is the lowest-latency option. A future Kafka-fronted ingest could swap in true RTM without changing the
              rest of this diagram.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}

const PIPELINE_PILL_COLORS: Record<string, string> = {
  blue: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300',
  amber: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  orange: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-300',
  teal: 'bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-300',
  emerald: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
  violet: 'bg-violet-100 text-violet-800 dark:bg-violet-900/30 dark:text-violet-300',
  sky: 'bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-300',
  red: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300',
};

function Pill({ color, children }: { color: keyof typeof PIPELINE_PILL_COLORS; children: ReactNode }) {
  return (
    <span
      className={`rounded-md px-3 py-1.5 font-medium ${PIPELINE_PILL_COLORS[color] ?? 'bg-muted text-muted-foreground'}`}
    >
      {children}
    </span>
  );
}

function Arrow() {
  return <span className="text-muted-foreground">→</span>;
}
