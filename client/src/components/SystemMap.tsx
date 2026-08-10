/**
 * The System Map: one app, two branches, and what powers every leaf.
 *
 * Shaped as a capability tree rather than a data-flow diagram. The earlier
 * version drew surfaces → orchestrator → domains → foundation, which reads as
 * plumbing and buried the fact reviewers actually react to: that each
 * specialist queries its OWN governed Genie space. Here that fact is a chip on
 * the specialist's own row, where the eye lands.
 *
 * Chips, not a single badge, because a lone "GENIE" badge misrepresented the
 * specialists: Genie is one tool of five to seven, and the badge duplicated the
 * domain the footer already named. Three categories — governed Genie space,
 * deterministic solvers, plain functions — keep the card to three lines while
 * making a Schedule Optimizer look like a solver host rather than a Genie
 * wrapper. Counts, not tool names; the optimizer card enumerates the seven.
 *
 * Two branches, not three. The reference this borrows its grammar from has a
 * third "always-on agents" column; we have no agent that runs without a user
 * asking, so there is no such column. Drawing one would be a claim rather than
 * a description — the same reason a domain whose Genie space did not provision
 * is drawn as SQL-only instead of quietly showing a Genie badge.
 *
 * Domain wiring and models are read from the server, so the map describes this
 * deployment rather than the design.
 */
import type { ReactNode } from 'react';

import type { Specialist } from '../lib/specialistCatalog';
import {
  optimizerMethodList,
  OPTIMIZER_TOOLS,
  readsLineOps,
  SPECIALISTS,
  toolMix,
} from '../lib/specialistCatalog';
import { reachSummary, spaceCountLabel } from '../lib/topologyCopy';
import type { ModelRoster, RoleModel } from '../lib/useModelRoster';
import { useModelRoster } from '../lib/useModelRoster';
import type { GovernedDomain, Topology } from '../lib/useTopology';
import { useTopology } from '../lib/useTopology';
import DatabricksIcon from './DatabricksIcon';

/** Accent per governed domain, so a domain keeps one colour across the map. */
const DOMAIN_ACCENT: Record<string, string> = {
  asset: 'bg-amber-500',
  quality: 'bg-rose-500',
  inventory: 'bg-emerald-500',
  production: 'bg-sky-500',
};

/**
 * Colour identifies which Genie space, so every chip reads `<space> genie` and
 * the colour tells you which one. Counts stay grey and recede.
 *
 * Line ops keeps full colour rather than being demoted, because it earns it: 14
 * curated tables against 12 across all four governed domains combined. The
 * earlier label was "live genie", which was wrong twice — it implied the other
 * five specialists cannot see the current shift (all eight read the live tables
 * with SQL), and it set "live" against "governed" as if they were two kinds of
 * Genie rather than two scopes of one.
 */
const DOMAIN_CHIP: Record<string, string> = {
  asset: 'bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300',
  quality: 'bg-rose-100 text-rose-800 dark:bg-rose-900/40 dark:text-rose-300',
  inventory: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300',
  production: 'bg-sky-100 text-sky-800 dark:bg-sky-900/40 dark:text-sky-300',
};
const OPS_CHIP = 'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300';
const COUNT_CHIP = 'bg-muted text-muted-foreground';

interface Chip {
  label: string;
  cls: string;
}

const CONTROL_TOWER = [
  {
    name: 'Line Status',
    desc: 'Animated schematic, every machine drawn as itself, plus KPIs and inventory',
    tag: 'SSE · LAKEBASE · SUB-SECOND',
    accent: 'bg-sky-500',
  },
  {
    name: 'Events & AI',
    desc: 'Incidents as the cascade propagates them, with ask-about-this',
    tag: 'SSE · EVENT LOG',
    accent: 'bg-rose-500',
  },
  {
    name: 'Cost & Obs',
    desc: 'In-app counters checked against system.billing.usage',
    tag: 'MLFLOW · SYSTEM TABLES',
    accent: 'bg-violet-500',
  },
  {
    name: 'Data Integrity',
    desc: 'Quality-gate pass rate and reject reasons, plus plane reconciliation',
    tag: 'QC GATE · DQX · RECONCILE',
    accent: 'bg-emerald-500',
  },
];

/**
 * One card. Chips sit on the same row as the tag so a specialist still fits in
 * three lines: what it is, what it does, what it carries and what runs it.
 */
function Leaf({
  name,
  desc,
  tag,
  accent,
  chips,
}: {
  name: string;
  desc: string;
  tag?: string;
  accent: string;
  chips?: Chip[];
}) {
  return (
    <div className="relative rounded-lg border bg-background py-2 pl-3 pr-2.5">
      <span className={`absolute inset-y-1.5 left-0 w-[3px] rounded-full ${accent}`} />
      <div className="min-w-0 text-xs font-semibold text-foreground">{name}</div>
      <div className="mt-0.5 text-[10px] leading-snug text-muted-foreground">{desc}</div>
      {chips?.length || tag ? (
        <div className="mt-1.5 flex flex-wrap items-center gap-1">
          {chips?.map((c) => (
            <span
              key={c.label}
              className={`rounded px-1.5 py-px text-[9px] font-bold uppercase tracking-wider ${c.cls}`}
            >
              {c.label}
            </span>
          ))}
          {tag ? (
            <span className="ml-auto pl-1 text-[9px] font-semibold uppercase tracking-wider text-muted-foreground/80">
              {tag}
            </span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Branch({
  index,
  title,
  icon,
  subtitle,
  topBar,
  className = '',
  children,
}: {
  index: number;
  title: string;
  icon: string;
  subtitle: string;
  topBar: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={`flex flex-col overflow-hidden rounded-xl border bg-card shadow-sm ${className}`}>
      <div className={`h-1 w-full ${topBar}`} />
      <div className="flex flex-1 flex-col p-4">
        <div className="text-[9px] font-bold uppercase tracking-[0.18em] text-muted-foreground">
          Branch {index}
        </div>
        <h4 className="mt-1 text-sm font-bold text-foreground">
          <span className="mr-1.5">{icon}</span>
          {title}
        </h4>
        <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">{subtitle}</p>
        {children}
      </div>
    </div>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="mt-3 border-t pt-2 text-[9px] font-bold uppercase tracking-[0.12em] text-muted-foreground">
      {children}
    </div>
  );
}

/**
 * What the specialist carries, in three categories rather than one badge.
 *
 * The earlier version badged every tile "GENIE", which was both incomplete and
 * redundant — Genie is one tool of five to seven, and the footer already named
 * the domain. Counts rather than tool names keep the card to three lines; the
 * optimizer card below the grid is where the seven solvers are enumerated.
 */
function specialistChips(
  s: Specialist,
  domain: GovernedDomain | undefined,
  model: RoleModel | undefined
): { chips: Chip[]; accent: string; tag?: string } {
  const chips: Chip[] = [];
  let accent = 'bg-slate-400';

  if (domain?.configured) {
    chips.push({ label: `${domain.domain} genie`, cls: DOMAIN_CHIP[domain.domain] ?? COUNT_CHIP });
    accent = DOMAIN_ACCENT[domain.domain] ?? accent;
  } else if (domain) {
    // A domain that did not provision is drawn as absent, never as a quiet
    // Genie badge — the map describes the deployment, not the design. It says
    // what the specialist HAS rather than what it lacks: an environment that
    // runs without the governed model is a supported configuration, and seven
    // tiles reading "not provisioned" made a working deployment look broken.
    chips.push({ label: 'sql only', cls: COUNT_CHIP });
  }

  if (readsLineOps(s)) {
    chips.push({ label: 'line ops genie', cls: OPS_CHIP });
    // Shift Briefing owns no governed domain: its subject is the shift that
    // just happened, so line ops is its only reach and its colour.
    if (!domain) accent = 'bg-teal-500';
  }

  const { optimizers, functions } = toolMix(s);
  if (optimizers > 0) {
    chips.push({
      label: `${optimizers} optimizer${optimizers === 1 ? '' : 's'}`,
      cls: COUNT_CHIP,
    });
  }
  chips.push({ label: `${functions} functions`, cls: COUNT_CHIP });

  return { chips, accent, tag: model ? model.display.toUpperCase() : undefined };
}

/**
 * Both feeds are optional props so a parent that already holds them can pass
 * them down. The About tab does, and without this it and this component each
 * called `/api/ops/topology` and `/api/ops/models` — four requests per visit,
 * repeated on every tab switch, because the tab is conditionally rendered and
 * so remounts. `/api/ops/models` lists the workspace's serving endpoints on a
 * cold call, and two concurrent first calls can both miss the server's cache.
 */
interface SystemMapProps {
  topology?: Topology | null;
  topologyError?: string | null;
  roster?: ModelRoster | null;
  rosterError?: string | null;
}

export default function SystemMap(props: SystemMapProps = {}) {
  // Rendered on its own (the README preview harness) it still fetches for
  // itself; given props it does not, and the hooks skip the request rather
  // than being skipped themselves, which the rules of hooks would not allow.
  const standalone = props.topology === undefined && props.roster === undefined;
  const fetchedTopology = useTopology(standalone);
  const fetchedRoster = useModelRoster(standalone);

  const topology = standalone ? fetchedTopology.topology : (props.topology ?? null);
  const topologyError = standalone ? fetchedTopology.error : (props.topologyError ?? null);
  const roster = standalone ? fetchedRoster.roster : (props.roster ?? null);
  const rosterError = standalone ? fetchedRoster.error : (props.rosterError ?? null);

  const modelByRole = new Map((roster?.roles ?? []).map((r) => [r.role, r]));
  const router = modelByRole.get('intent_router');

  const domainBySpecialist = new Map<string, GovernedDomain>();
  for (const domain of topology?.domains ?? []) {
    for (const role of domain.specialists) domainBySpecialist.set(role, domain);
  }

  const provisioned = topology?.configured ?? 0;
  const genieSpaces = provisioned + (topology?.streaming_space ? 1 : 0);

  return (
    <section className="rounded-lg border bg-card p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
          System Map
        </h3>
        {topology ? (
          <span className="text-[11px] text-muted-foreground">
            {spaceCountLabel(genieSpaces)}
          </span>
        ) : null}
      </div>

      {/* Root */}
      <div className="mx-auto mt-4 max-w-md rounded-xl bg-gradient-to-b from-slate-800 to-slate-900 px-5 py-3 text-center text-white shadow-md dark:from-slate-700 dark:to-slate-800">
        <div className="text-[9px] font-bold uppercase tracking-[0.18em] text-white/60">
          Databricks App · FastAPI + React
        </div>
        <div className="mt-1 flex items-center justify-center gap-2 text-base font-bold">
          <DatabricksIcon className="h-4 w-4" />
          ProdLine CoPilot
        </div>
        <div className="mt-0.5 text-[10px] text-white/70">
          Single sign-on · Service-principal auth · Lakehouse-native UI
        </div>
      </div>

      {/* One trunk from the root, splitting to the two branch columns.
          The drop row reuses the branch grid, so each drop sits on its column's
          true centre whatever the width — computing the percentages by hand put
          them a few pixels off, because the grid gap shifts the centres. The
          two bar halves each reach 8px into the 16px gap and meet there. */}
      <div className="hidden md:block" aria-hidden="true">
        <div className="mx-auto h-3 w-px bg-border" />
        <div className="grid grid-cols-3 gap-4">
          <div className="relative h-3">
            <span className="absolute left-1/2 top-0 h-full w-px bg-border" />
            <span className="absolute left-1/2 top-0 h-px -right-2 bg-border" />
          </div>
          <div className="relative col-span-2 h-3">
            <span className="absolute left-1/2 top-0 h-full w-px bg-border" />
            <span className="absolute -left-2 right-1/2 top-0 h-px bg-border" />
          </div>
        </div>
      </div>
      <div className="mx-auto h-4 w-px bg-border md:hidden" aria-hidden="true" />

      {/* Branch 2 carries eleven leaves to branch 1's four, so it gets two
          thirds of the width and lays its specialists out two-up. Equal columns
          left branch 1 as a tall empty box. */}
      <div className="grid gap-4 md:grid-cols-3">
        {/* Branch 1 — the operational surface, no model involved */}
        <Branch
          index={1}
          icon="🗼"
          title="Control Tower"
          subtitle="What the line is doing right now — streamed, deterministic, no model in the path"
          topBar="bg-sky-500"
        >
          {/* Counted, not typed. This label said "4" while the app had five,
              because adding a tab elsewhere cannot reach a number written here. */}
          <SectionLabel>{CONTROL_TOWER.length} operational tabs</SectionLabel>
          <div className="mt-2 space-y-1.5">
            {CONTROL_TOWER.map((t) => (
              <Leaf key={t.name} {...t} />
            ))}
          </div>
          <p className="mt-3 text-[10px] leading-relaxed text-muted-foreground">
            Nothing here waits on an LLM. Telemetry streams from Lakebase over SSE and the quality
            gate is plain SQL predicates, so the picture stays sub-second and reproducible. Any tile
            can hand its context to the copilot with one click.
          </p>
        </Branch>

        {/* Branch 2 — the reasoning surface, one governed space per specialist */}
        <Branch
          index={2}
          icon="🧠"
          title="Specialist Copilot"
          subtitle="Ask a question; the router picks the specialist that owns the problem"
          topBar="bg-violet-500"
          className="md:col-span-2"
        >
          <div className="mt-3 rounded-lg border border-primary/30 bg-primary/5 px-3 py-2">
            <div className="flex items-start justify-between gap-2">
              <div className="text-xs font-semibold text-foreground">Orchestrator</div>
              <span className="shrink-0 rounded bg-muted px-1.5 py-px text-[9px] font-bold uppercase tracking-wider text-muted-foreground">
                {router ? router.display : 'LangGraph'}
              </span>
            </div>
            <div className="mt-0.5 text-[10px] leading-snug text-muted-foreground">
              Classifies intent, streams a snapshot within a second, then hands off to one
              specialist
            </div>
          </div>

          <SectionLabel>
            {SPECIALISTS.length} specialist agents · genie + optimizers + functions
          </SectionLabel>
          <p className="mt-1.5 text-[10px] leading-snug text-muted-foreground">
            Every one is a tool loop, not a prompt. All eight carry direct SQL, a calculator and
            the shift plan; the chips show what each adds on top.
          </p>
          {/* Said out loud rather than swallowed: without the roster every model
              tag below simply vanishes and the Orchestrator badge falls back to
              reading "LangGraph", which looks deliberate. */}
          {rosterError && (
            <div className="mt-2 rounded-lg border border-dashed bg-muted/20 px-3 py-2 text-[10px] text-muted-foreground">
              Model names unavailable — {rosterError}
            </div>
          )}
          {topologyError ? (
            <div className="mt-2 rounded-lg border border-dashed bg-muted/20 px-3 py-2 text-[10px] text-muted-foreground">
              Governed domain wiring unavailable — {topologyError}
            </div>
          ) : (
            <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
              {SPECIALISTS.map((s) => {
                const reach = specialistChips(
                  s,
                  domainBySpecialist.get(s.id),
                  modelByRole.get(s.id)
                );
                return (
                  <Leaf
                    key={s.id}
                    name={`${s.icon} ${s.name}`}
                    desc={s.short}
                    accent={reach.accent}
                    chips={reach.chips}
                    tag={reach.tag}
                  />
                );
              })}
            </div>
          )}

          <SectionLabel>+ also in this branch</SectionLabel>
          <div className="mt-2 grid gap-1.5">
            <Leaf
              name={`${OPTIMIZER_TOOLS.size} deterministic optimizers`}
              desc={`Behind the optimizer chips above: ${optimizerMethodList()}`}
              accent="bg-amber-500"
              chips={[{ label: 'no llm in the math', cls: COUNT_CHIP }]}
              tag="CALLED BY SPECIALISTS"
            />
          </div>

          <p className="mt-3 text-[10px] leading-relaxed text-muted-foreground">
            {reachSummary(provisioned, topology?.total ?? 4)}
          </p>
        </Branch>
      </div>

      {topology && !topology.mvm_silver_enabled ? (
        <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
          Streaming silver is off in this environment, so the governed tables are not being written
          here. The domains above describe the model; the rows come from whichever environment owns
          the write path.
        </p>
      ) : null}
    </section>
  );
}
