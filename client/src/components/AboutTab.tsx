/**
 * Renders the architecture explainer tab for specialists, tools, platform, and pipeline tiers.
 * Used from `App` as the non-operational context page for demos and technical reviewers.
 * Text intentionally documents the tactical dual-write demo path and the preferred Synced Tables production pattern.
 */
import type { ReactNode } from 'react';

import { rosterStats } from '../lib/modelRosterView';
import { OPTIMIZER_TOOLS, SPECIALISTS } from '../lib/specialistCatalog';
import { genieSpacePhrase, genieSpaceRole, GOVERNED_DOMAINS } from '../lib/topologyCopy';
import type { ModelRoster } from '../lib/useModelRoster';
import { useModelRoster } from '../lib/useModelRoster';
import type { GovernedDomain } from '../lib/useTopology';
import { useTopology } from '../lib/useTopology';
import DatabricksIcon from './DatabricksIcon';
import SystemMap from './SystemMap';

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
    name: 'Line Ops Genie',
    icon: '✨',
    desc: 'Natural language questions against the curated space over the live streaming tables — the shift as it is happening. Broader than a governed domain by design: 14 tables behind a free-form surface, where the governed model splits along the schemas its grants follow. Carried by the three specialists that reason across a whole shift.',
    platform: 'AI/BI Genie',
  },
  {
    id: 'governed_domain_query',
    name: 'Governed Domain Genie',
    icon: '🏛️',
    desc: 'One curated space per governed schema — asset, quality, inventory, production — and a specialist is handed only its own. Narrow scope is what keeps Genie accurate, and the boundary is a Unity Catalog schema, so per-domain grants are real rather than diagrammatic. Genie cannot join across spaces, so cross-domain questions go to SQL instead.',
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
  // `role` is filled in at render from the topology endpoint — see genieSpaceRole.
  { name: 'AI/BI Genie', role: '', icon: 'genie' },
  { name: 'Delta Lake', role: 'Bronze + Gold tables backing both the live and analytics paths', icon: 'delta' },
  { name: 'Unity Catalog', role: 'Governance, lineage, access control, model registry', icon: 'uc' },
  {
    name: 'Zerobus',
    role: 'Tier-2 gRPC Bronze ingest from the producer (analytics path; ~5-30 s materialization SLA)',
    icon: 'zerobus',
  },
  {
    name: 'Spark Structured Streaming',
    role: 'Continuous quality-gate job (streaming_quality_sync): validates Bronze, writes Gold + Lakebase, records rejects, and (streaming silver) MERGEs into the governed industry model at seconds latency',
    icon: 'lakeflow',
  },
  {
    name: 'Manufacturing Industry Model',
    role: 'Governed silver model (asset / production / quality / inventory) mirroring a canonical enterprise schema; each domain has its own curated Genie space, and because the four schemas share one catalog, SQL can join across them where Genie cannot',
    icon: 'mvm',
  },
  {
    name: 'DQX (Databricks Labs)',
    role: 'Optional data-quality checks (freshness / range / referential) flagged into a dqx_warnings audit table; observability-only, never gates the pipeline',
    icon: 'dqx',
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
  mvm: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-300',
  dqx: 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300',
  serving: 'bg-fuchsia-100 text-fuchsia-700 dark:bg-fuchsia-900/30 dark:text-fuchsia-300',
  vector: 'bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-300',
  lakebase: 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300',
  mlflow: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  dabs: 'bg-slate-100 text-slate-700 dark:bg-slate-800/50 dark:text-slate-300',
};

function PlatformTag({ name }: { name: string }) {
  return (
    <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
      <DatabricksIcon className="h-3 w-3" />
      {name}
    </span>
  );
}

/** Domain tint, matching the System Map above so a domain keeps one colour. */
const DOMAIN_BADGE: Record<string, string> = {
  asset: 'bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-300',
  quality: 'bg-rose-100 text-rose-800 dark:bg-rose-900/30 dark:text-rose-300',
  inventory: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-300',
  production: 'bg-sky-100 text-sky-800 dark:bg-sky-900/30 dark:text-sky-300',
};

/**
 * The specialist's governed Genie space. Separate from ToolBadge because this
 * one is not in the static tool list: which domains exist is a property of the
 * deployment, so an unprovisioned space must show as absent rather than be
 * asserted from a hardcoded roster.
 *
 * An absent domain says what the specialist HAS, not what it lacks. Striking
 * the badge through was the obvious rendering and the wrong one: prod runs
 * without the governed model by design, so every specialist showed a crossed-out
 * domain and a supported configuration read as a broken one. The System Map hit
 * this first and settled on `sql only`; this is the same answer, so the two
 * sections of the same page agree.
 */
function DomainBadge({ domain }: { domain: GovernedDomain }) {
  if (!domain.configured) {
    return (
      <span
        className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
        title={`The governed ${domain.domain} domain is not provisioned in this deployment, so this specialist reaches its data with direct SQL instead.`}
      >
        sql only
      </span>
    );
  }
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${
        DOMAIN_BADGE[domain.domain] ?? 'bg-muted text-muted-foreground'
      }`}
      title={`Dedicated Genie space over the governed ${domain.domain} domain: ${domain.entities.join(', ')}`}
    >
      🏛️ {domain.domain} domain
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

/**
 * A pointer to the Multi-AI tab, which owns the roster in full.
 *
 * The table used to live here. It moved because it had outgrown a section on a
 * page about something else, and duplicating it in two places is how the two
 * copies end up disagreeing.
 */
function ModelRosterSummary({ roster }: { roster: ModelRoster | null }) {
  if (!roster) return null;
  // Same count as the Multi-AI tab's headline stat, from the same helper, so
  // the two pages cannot state different numbers of vendors.
  const vendorCount = rosterStats(roster).vendorCount;
  const vendors = Array.isArray(roster.vendors) ? roster.vendors : [];

  return (
    <section className="rounded-lg border bg-card p-4">
      <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        Model Roster
      </h3>
      <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
        {roster.multi_vendor
          ? `Each role runs on the model that suits its work — ${vendorCount} vendors answering here, behind one API, one permission model and one bill.`
          : 'Multi-vendor routing is disabled (MODEL_MULTI_VENDOR=0) — every role runs on the two fallback tiers.'}{' '}
        The full roster, the reasoning behind each pick and this workspace&rsquo;s coverage are on
        the <strong className="text-foreground">Multi-AI</strong> tab.
      </p>
      <div className="mt-2.5 flex flex-wrap gap-1.5">
        {vendors.map((v) => (
          <span
            key={v}
            className="rounded-full bg-primary/10 px-2.5 py-0.5 text-[11px] font-medium text-primary"
          >
            {v}
          </span>
        ))}
      </div>
    </section>
  );
}

export default function AboutTab() {
  // Fetched once here and passed down to SystemMap, which would otherwise ask
  // for both again on every mount of this tab.
  const { roster, error: rosterError } = useModelRoster();
  const { topology, error: topologyError } = useTopology();
  const modelByRole = new Map((roster?.roles ?? []).map((r) => [r.role, r]));
  // Until topology arrives, state the full wiring rather than flash a smaller
  // number and correct itself a moment later.
  const genieSpaces = topology ? topology.configured + (topology.streaming_space ? 1 : 0) : 5;

  const domainBySpecialist = new Map<string, GovernedDomain>();
  for (const domain of topology?.domains ?? []) {
    for (const role of domain.specialists) domainBySpecialist.set(role, domain);
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="text-center">
        <h2 className="text-xl font-bold text-foreground">ProdLine CoPilot</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          A compound AI system for production line management — {SPECIALISTS.length} specialist agents, 7 tools
          across {genieSpacePhrase(genieSpaces)}, {OPTIMIZER_TOOLS.size} mathematical optimizers, a governed
          Manufacturing industry model, and optional DQX data-quality checks, built on the Databricks Data
          Intelligence Platform.
        </p>
      </div>

      {/* Leads the page: the agent-to-governed-data topology is the thing that
          explains the architecture fastest, and the sections below detail it. */}
      <SystemMap
        topology={topology}
        topologyError={topologyError}
        roster={roster}
        rosterError={rosterError}
      />

      <ModelRosterSummary roster={roster} />

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
                    <div className="text-[11px] text-muted-foreground">
                      {modelByRole.get(s.id)
                        ? `${modelByRole.get(s.id)!.display} · ${modelByRole.get(s.id)!.vendor}`
                        : '—'}
                    </div>
                  </div>
                </div>
                <p className="mb-3 text-xs leading-relaxed text-muted-foreground">{s.domain}</p>
                <div className="flex flex-wrap gap-1">
                  {/* The governed space leads: it is the tool that decides which
                      slice of the enterprise schema this specialist can reach. */}
                  {domainBySpecialist.has(s.id) ? (
                    <DomainBadge domain={domainBySpecialist.get(s.id)!} />
                  ) : null}
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
            const role =
              p.icon === 'genie'
                ? genieSpaceRole(topology?.configured ?? GOVERNED_DOMAINS, topology?.streaming_space ?? true)
                : p.role;
            return (
              <div key={p.name} className="flex items-start gap-3 rounded-md border bg-card p-3">
                <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${colors}`}>
                  <DatabricksIcon className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <div className="text-xs font-semibold text-foreground">{p.name}</div>
                  <div className="mt-0.5 text-[11px] text-muted-foreground">{role}</div>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Data Pipeline */}
      <section>
        <h3 className="mb-1 text-sm font-semibold uppercase tracking-wider text-muted-foreground">Data Pipeline</h3>
        <p className="mb-3 text-xs leading-relaxed text-muted-foreground">
          <strong className="text-foreground">Two tiers</strong>, fed by one producer that
          dual-writes at the source. Tier 1 carries the live UI; Tier 2 carries analytics and the
          governed model. Everything indented under Tier 2 is a branch of that same streaming job,
          not a third pipeline.
        </p>
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
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-foreground">
              Tier 1 · serving (sub-second live UI)
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
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-foreground">
              Tier 2 · analytics + governed lakehouse
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
              <Pill color="sky">Line ops Genie space</Pill>
              <Arrow />
              <Pill color="red">AI agents</Pill>
            </div>
          </div>
          {/*
            There are TWO tiers, not four. Both blocks below are branches of the
            Tier-2 structured-streaming job above — the same job that writes Gold
            also MERGEs each micro-batch into the governed model (design B, so the
            industry schema rides the live path at seconds latency instead of a
            batch refresh) and runs the QC gate. They are indented rather than
            given their own headings, because sibling headings read as extra
            tiers and invite the question.
          */}
          <div className="ml-3 border-l-2 border-muted pl-3">
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
              ↳ Same streaming job, two further sinks
            </div>

            <div className="mb-1 text-[10px] font-medium text-muted-foreground">
              Governed industry model (streaming silver)
              {/* Prod runs with this sink switched off, and a diagram of a
                  pipeline that is not running reads as a claim that it is. The
                  System Map says the same thing at the foot of its own section. */}
              {topology && !topology.mvm_silver_enabled && (
                <span className="ml-1.5 font-normal normal-case text-muted-foreground/70">
                  — not enabled in this environment; the rows come from whichever environment owns
                  the write path
                </span>
              )}
            </div>
            <div
              className={`flex flex-wrap items-center gap-2 ${
                topology && !topology.mvm_silver_enabled ? 'opacity-50' : ''
              }`}
            >
              <Pill color="orange">Streaming MERGE (silver_mapper)</Pill>
              <Arrow />
              <Pill color="cyan">asset / production / quality / inventory</Pill>
              <Arrow />
              <Pill color="sky">
                {topology?.configured ?? GOVERNED_DOMAINS} domain Genie space
                {(topology?.configured ?? GOVERNED_DOMAINS) === 1 ? '' : 's'}
              </Pill>
              <Arrow />
              <Pill color="red">Domain specialists</Pill>
            </div>

            <div className="mb-1 mt-2.5 text-[10px] font-medium text-muted-foreground">
              Quality gate (observability-only)
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Pill color="orange">Streaming QC gate</Pill>
              <Arrow />
              <Pill color="rose">DQX warnings (optional)</Pill>
              <Arrow />
              <Pill color="emerald">dqx_warnings audit</Pill>
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
            <p>
              <span className="font-medium text-foreground">Governed model honesty.</span> The industry model mirrors a
              canonical enterprise schema, but only a curated column subset is populated by the mapper (others are
              intentionally <code>NULL</code>). Incident-driven facts (<code>asset.failure_record</code>,{' '}
              <code>quality.inspection_result</code>, <code>quality.ncr</code>) are empty at steady state and fill only
              during incidents. For real-time monitoring the agents prefer the live streaming tables — the governed
              model is for enterprise-term, audit-grade questions.
            </p>
            <p>
              <span className="font-medium text-foreground">DQX is observability-only.</span> When enabled, DQX flags
              suspect rows into a separate <code>dqx_warnings</code> audit table beside the QC gate; it never changes the
              valid/reject decision and never takes the streaming job down. Off by default.
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
  cyan: 'bg-cyan-100 text-cyan-800 dark:bg-cyan-900/30 dark:text-cyan-300',
  rose: 'bg-rose-100 text-rose-800 dark:bg-rose-900/30 dark:text-rose-300',
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
