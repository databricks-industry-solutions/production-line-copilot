/**
 * The words a presenter says, kept out of the component that renders them.
 *
 * Same split as `specialistCatalog`: the copy is data, so it can be asserted
 * against directly rather than fished out of JSX. It earns the separation
 * here for a second reason — every count below is derived from the catalogs
 * the rest of the app renders from, never typed in. A guide is the one screen
 * a presenter reads out loud, so a stale number is said to a customer rather
 * than merely displayed, and the test that pins this can only see it if the
 * script is importable.
 *
 * What is deliberately absent is ROI. The README declines to assert dollar
 * figures because the data is synthetic and the value is line-specific, and a
 * talk track is precisely where an invented payback number would acquire
 * authority. `HONEST_ANSWERS` hands the presenter the truthful replies to the
 * three questions this demo reliably provokes instead.
 */
import { MACHINE_LAYOUT } from './constants';
import type { TabId } from './tabRouting';

export interface DemoStep {
  /** Stable key, and the anchor a future deep link into a step would use. */
  id: string;
  /** Where the step's button sends the audience. */
  tab: TabId;
  /** The tab's own label, so the button says where it goes. */
  tabLabel: string;
  title: string;
  /** Seconds. Summed for the header rather than restated there. */
  seconds: number;
  /** Said out loud. Written to be read verbatim without sounding written. */
  script: string;
  /** What to physically point at while saying it. */
  pointAt: string[];
  /** Databricks products this beat puts on screen. */
  products: string[];
}

/**
 * The walkthrough, in order.
 *
 * Seven beats over six tabs — the line gets two, because watching a cascade
 * propagate is the moment the product lands and it should not share a beat
 * with the tour of the line that precedes it. The second beat is where the
 * damage acquires a direction, and it only means anything once something is
 * broken.
 */
export const DEMO_STEPS: DemoStep[] = [
  {
    id: 'line-at-rest',
    tab: 'tower',
    tabLabel: 'Line Status',
    title: 'Start with a line that is simply running',
    seconds: 55,
    script: `This is a bottling line mid-shift — ${MACHINE_LAYOUT.length} machines, drawn the way the line is actually built rather than as a row of tiles. Every machine is its own equipment, so you can find the filler by its shape without reading a label, and the conveyors are the belts between them instead of boxes pretending to be process steps. The dashes on each belt move at the speed that section is really running. Everything green is running, the numbers at the top are OEE, availability, performance and quality, and all of it updates about once a second. Nothing here is a mock-up: the schematic is reading a serving table, not a simulation held in the web server's memory.`,
    pointAt: [
      'Machines drawn as equipment — the filler bowl, the palletiser arm',
      'The dashed feeds dropping in: the materials each machine consumes',
      'The KPI strip refreshing without a page reload',
      'The source badge on each panel — SCADA, MES, SAP, LIMS, CMMS, WMS',
    ],
    products: ['Zerobus', 'Unity Catalog', 'Lakebase', 'Databricks Apps'],
  },
  {
    id: 'cascade',
    tab: 'tower',
    tabLabel: 'Line Status',
    title: 'Break it on purpose, and watch the damage spread',
    seconds: 85,
    script:
      "I'll stop the filler. Watch the belts either side of it stop moving, and watch how far the stillness spreads. Downstream goes grey as the buffers drain and those machines run out of work. Upstream goes amber and then stops, because their buffers fill and they have nowhere to put anything. The schematic also separates the two failures that look identical on an alarm list: starved means nothing is arriving, so the fault is upstream of that machine; blocked means it cannot discharge, so the fault is downstream. That is the difference between sending a fitter to the right end of the line and the wrong one. And that is the part a single-machine alarm never tells you — one fault, and within a minute much of the line has stopped producing. The cascade is computed in a job on the platform, so it is the tables that change and the screen just follows.",
    pointAt: [
      'Belts that have stopped moving, either side of the fault',
      'A starved machine and a blocked one, pointing in opposite directions',
      'OEE falling in the KPI strip as it happens',
      'The event appearing in the feed within a second or two',
    ],
    products: ['Zerobus', 'Unity Catalog', 'Lakebase'],
  },
  {
    id: 'ask',
    tab: 'alerts',
    tabLabel: 'Events & AI',
    title: 'Ask what happened, in the words you would actually use',
    seconds: 75,
    script:
      'Now I ask it what happened, the way I would ask a colleague. A router reads the question and hands it to the one specialist that owns this kind of problem — here the downtime analyst. What comes back is a root cause and a ranked set of recovery actions, and underneath it the evidence: the queries it ran and the rows it saw. The actions are proposals. One click copies a ready-to-paste handoff for whoever owns the change.',
    pointAt: [
      'The specialist chosen, and the model badge on the answer',
      'The evidence trail under the response',
      'The action chips — proposals, not writes',
    ],
    products: ['Foundation Model APIs', 'AI/BI Genie', 'Vector Search', 'Databricks SQL'],
  },
  {
    id: 'models',
    tab: 'multiai',
    tabLabel: 'Multi-AI',
    title: 'One question, whichever vendor suits it',
    seconds: 45,
    script:
      'Each role runs on the model that suits its job, across six vendors, behind one API, one permission model and one bill. This page is not a slide — it resolves what is actually serving in this workspace right now, and if a preferred model is not available here it shows the role falling back rather than pretending.',
    pointAt: ['The vendor spread, read live from the workspace', 'Any role shown falling back to a default'],
    products: ['Foundation Model APIs', 'Unity Catalog'],
  },
  {
    id: 'governance',
    tab: 'about',
    tabLabel: 'About',
    title: 'Show the governance holding it up',
    seconds: 45,
    script:
      'This is the part that survives a security review. Each specialist reaches its own governed Genie space, and the boundary is a Unity Catalog schema — so the permissions are real grants rather than a diagram. Narrow scope is also what keeps Genie accurate. A domain that was never provisioned in this workspace is drawn as SQL-only instead of showing a badge it has not earned.',
    pointAt: [
      'The System Map, read from the deployed wiring rather than the design',
      'One governed domain per schema, and which specialists reach it',
    ],
    products: ['Unity Catalog', 'AI/BI Genie', 'Databricks SQL'],
  },
  {
    id: 'cost',
    tab: 'ops',
    tabLabel: 'Cost & Obs',
    title: 'Say what the answer cost',
    seconds: 40,
    script:
      'Every answer you just saw has a token cost and a trace attached. This is usually the question that arrives right after the wow, and it is better to answer it before it is asked: here is the spend, and here is the full reasoning trace in MLflow for any single response.',
    pointAt: ['Cost per response', 'The link through to the MLflow trace'],
    products: ['MLflow', 'Foundation Model APIs'],
  },
  {
    id: 'trust',
    tab: 'quality',
    tabLabel: 'Data Integrity',
    title: 'Close on whether to believe any of it',
    seconds: 40,
    script:
      'Last thing, and it is the one a data team cares most about. This tab reconciles what the app is showing against what is in the tables, and it will tell you when a table has gone empty rather than quietly rendering zeros. An empty table returning no rows instead of an error is how a confident dashboard ends up lying, so we made that visible on purpose.',
    pointAt: ['The reconciliation between the app and the tables', 'The embedded quality dashboard'],
    products: ['Unity Catalog', 'Databricks SQL'],
  },
];

/**
 * The three questions this demo provokes, and honest answers to them.
 *
 * A presenter who did not build this needs the limits as much as the features:
 * the fastest way to lose a technical room is to be caught overclaiming on the
 * one thing it already suspects.
 */
export const HONEST_ANSWERS: { question: string; answer: string }[] = [
  {
    question: '"Does it actually change anything in our systems?"',
    answer:
      'No, and that is deliberate. Every recommendation is a proposal, and the action button copies a handoff. There is no write path into a CMMS, SAP or a scheduler. The design for what approved write-back would take is written down and explicitly unimplemented — say that plainly, it reads as discipline rather than a gap.',
  },
  {
    question: '"Is this real data?"',
    answer:
      'The telemetry is synthetic and generated by a cascade model, and the whole pipeline underneath it is real — Zerobus into Unity Catalog, streamed to a Lakebase serving layer. What is being demonstrated is the architecture, not the plant.',
  },
  {
    question: '"What is the ROI?"',
    answer:
      'This accelerator asserts no payback figure, because the value of a downtime minute is specific to a line. The honest move is to hand them the levers — time to resolution, cascading losses, OEE stability — and put their own numbers against them.',
  },
];

/** Whole minutes, for the header. */
export function totalMinutes(steps: DemoStep[] = DEMO_STEPS): number {
  return Math.round(steps.reduce((sum, step) => sum + step.seconds, 0) / 60);
}
