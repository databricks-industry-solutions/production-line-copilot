# ProdLine CoPilot: v0.2.0 Release Plan

Status: Deployed + smoke-tested on dev (all themes complete and verified live)
Owner: mohammad.khelghati@databricks.com
Baseline: v0.1.0 (public: databricks-industry-solutions/production-line-copilot)
Last updated: 2026-08-06

Progress:
- [x] Theme 1: AppKit 0.48 modernization + deploy self-heal grants
- [x] Theme 2a: runnable + accurate guided notebook path (00..05)
- [x] Theme 2b: committed example bundle target; `bundle validate` passes on fresh clone
- [x] Theme 2c: architecture diagram embedded in README
- [x] Theme 3a: eval dataset grown (12 -> 33), forbidden-phrase scorer, orchestrator/specialist spans, docs/EVALUATION.md
- [x] Theme 3b: first-class `ask_line` Genie path + `/api/ask-line` + AskLinePanel UI with SQL provenance + eval case (since reverted — see Theme 3b below)
- [x] Dev deploy + live smoke test: root/UI, `/healthz`, live SSE stream, `/api/sim/status`,
      `/api/ask-line` (Genie answer+SQL+tables), `/api/agent` (live OEE) all 200. Two runtime
      bugs found + fixed (Genie Conversation API endpoint; sim/status datetime) + idempotent
      app-SP Genie grant added to deploy.sh.

## Goal

Modernize the platform runtime, remove first-deploy friction for external
adopters, and deepen the two capabilities that make the accelerator credible for
manufacturing customers: agent evaluation/observability and ad-hoc data Q&A via
Genie.

## In scope

1. Theme 1: AppKit modernization (0.11.1 to 0.48.0)
2. Theme 2: Frictionless adoption (runnable notebook path, validated example
   config, README architecture diagram)
3. Theme 3a: Expand MLflow eval harness + agent tracing coverage
4. Theme 3b: Deepen Genie integration for ad-hoc line questions

Out of scope for v0.2.0 (see Backlog): real-data connector example,
configurable line topology.

---

## Theme 1: AppKit modernization (0.11.1 to 0.48.0)

Impact: High (security currency + future-proofing). Effort: M. Risk: M (runtime).

Context: v0.1.0 pins AppKit 0.11.1 and clears all OTel advisories via npm
`overrides`. AppKit 0.48.0 was validated to build but was deferred because its
newer appkit-ui pulls modular echarts imports (`echarts-for-react/esm/core`,
introduced in appkit 0.43.1) that the vitest resolver cannot resolve, and it
introduces new advisories in its dependency tree. The production client build
already handles the echarts import, so the test failure is a vitest resolver
config gap, not a runtime bug.

Tasks:
- Upgrade `@databricks/appkit` and `@databricks/appkit-ui` to 0.48.0.
- Fix vitest resolution of `echarts-for-react/esm/core` (inline the dep via
  vitest `test.server.deps.inline`, or an equivalent resolve alias).
- Add `overrides` for the advisories AppKit 0.48 introduces: `drizzle-orm`
  (HIGH, SQL injection, fixed 0.45.2), `js-yaml` (HIGH, fixed 4.3.0), `echarts`
  (MODERATE XSS, fixed 6.1.0), `dompurify` (LOW, fixed 3.4.12), and confirm
  `@opentelemetry/propagator-jaeger` stays patched.
- Reconcile the existing OTel overrides (some may become redundant once AppKit
  ships newer OTel).
- Re-run OSV scan; target 0 advisories.

Acceptance criteria:
- `npm run build:server` emits `dist/server/server.js`; `npm run build:client`
  succeeds; `npx vitest run` is fully green (31+ tests).
- OSV scan reports 0 advisories; lockfile stays public npm only.
- Deploy to dev and smoke-test: chat + SSE stream, live KPI updates, Data
  Quality dashboard, and SQL/Genie queries all work against a real workspace.
- Rollback plan documented (revert to 0.11.1 + overrides is the known-good
  fallback).

---

## Theme 2: Frictionless adoption

Impact: High. Effort: M. Risk: Low.

### 2a. Runnable end-to-end notebook path
Today `scripts/deploy.sh` is the primary path; `notebooks/00_Parameters.py`
through `05_Validate.py` exist but are not verified as a single run-all flow.

Tasks:
- Make `notebooks/00..05` runnable end to end on serverless (parameterized,
  idempotent), or add a single orchestrator job that runs them in order.
- Ensure `03_Bootstrap_Demo_Data.py` matches the documented default data path.
- Document the notebook path as an alternative to `deploy.sh` in
  `docs/solution-accelerator/DEPLOY.md`.

Acceptance: a fresh workspace can go from clone to running app via either
`deploy.sh` or the notebook run-all, with no manual edits beyond
`config/project-settings.local.yaml`.

### 2b. Validated example config + bundle target
Tasks:
- Ship one filled, non-sensitive example `config/project-settings.local.yaml`
  and a validated example bundle target/host in `databricks.yml`.
- Add a note on how to adapt it.

Acceptance: `databricks bundle validate` passes against the example target.

### 2c. Embed architecture diagram in README
Tasks:
- Embed `docs/media/architecture-high-level.png` in README (currently only
  referenced from `docs/ARCHITECTURE.md`).

Acceptance: diagram renders on the public repo landing page.

---

## Theme 3a: Expand MLflow eval harness + agent tracing coverage

Impact: Med. Effort: M.

Context: `evaluations/` already has `run_eval.py`, `judges.py`, `dataset.py`,
`scenarios.py`, and `data/seed_v1.jsonl`. Every chat answer carries a link to
its own MLflow trace via `agent_server/trace_link.py`.

Tasks:
- Grow the eval dataset (`seed_v1.jsonl`) with cases per specialist and per
  incident scenario; add a `seed_v2` if needed.
- Add or refine LLM judges for the dimensions that matter (root-cause accuracy,
  action-chip relevance, groundedness against live telemetry).
- Ensure every specialist and tool call is traced (spans for orchestrator,
  each specialist, and each tool: sql_query, genie_query,
  similar_case_retriever).
- Add a documented `evaluations/run_eval.py` invocation and record baseline
  scores in `docs/ai/testing/`.

Acceptance: `run_eval.py` produces a scored report across all specialists;
traces are visible per run in MLflow; baseline metrics recorded.

---

## Theme 3b: Deepen Genie integration for ad-hoc line questions

Impact: Med. Effort: M.

Context: Genie is wired into `agent_server/agent.py`, `start_server.py`, and
several specialists (`oee_coach`, `strategic_advisor`, `shift_briefing`), with
config in `config/project-settings.yaml`.

Tasks:
- Add a first-class "ask the line" path that routes free-form data questions to
  Genie and returns grounded answers with the generated SQL.
- Curate the Genie space instructions/sample questions for the line schema so
  answers are reliable out of the box.
- Surface Genie answers in the UI with provenance (the SQL and tables used).
- Add eval cases (ties into Theme 3a) that cover Genie-answered questions.

Acceptance: a user can ask an ad-hoc question (for example, "which machine had
the most downtime this shift?") and get a correct, grounded answer with visible
SQL, validated by eval cases.

Outcome: shipped, then removed. The standalone panel asked its own Genie space
directly, which pointed at the "current" UC tables (`line_kpi`, `machine_state`,
and so on) — DDL that the streaming pipeline never writes, since it fills the
`*_bronze` and `gold_history_*` tables instead. So the panel answered ad-hoc
questions with empty result sets while the dashboard beside it showed live
numbers from Lakebase. The specialists reach Genie through the governed
per-domain tools and still do; only the standalone path is gone. Reinstating it
means giving it a Genie space pointed at tables that actually hold data.

---

## Suggested sequencing

1. Theme 1 (AppKit) first: it is the riskiest and everything else should be
   validated on the modern runtime.
2. Theme 2 in parallel where independent (2c README is trivial; 2a/2b touch
   deploy).
3. Theme 3a (eval/tracing) before 3b, so Genie work lands with eval coverage.

## Backlog (deferred beyond v0.2.0)

- **Judge alignment with real SME labels.** Workflow is now scaffolded and
  runnable in `evaluations/alignment/` (managed UC dataset, name-matched label
  schemas, Review App labeling session, MemAlign `align()`, and
  `run_eval --use-aligned-judges`). Remaining work is to *run it with real
  line-manager labels* — do not align to LLM-generated labels (circular).
  Today's judges remain unaligned until then. Impact: High. Effort: M (mostly SME
  time). See `evaluations/alignment/README.md`.
- **Production monitoring.** Register the eval scorers to run on sampled live app
  traces (not just the pre-deploy gate). Impact: Med. Effort: M.
- **~~MLflow trace export from Apps compute~~ — done.** The app could not reach
  `us-east-1.storage.cloud.databricks.com`, so trace metadata landed and every
  span was dropped. `scripts/deploy.sh` now provisions a Unity Catalog-backed
  experiment (`scripts/provision_trace_storage.py`), which writes spans to Delta
  through the workspace host the app already reaches. Applies to prod on its
  next deploy; the pre-existing bundle experiment keeps its old traces and is
  left in place, since an experiment that already holds traces cannot be
  converted.
- ~~**Load prompts from the registry at runtime.**~~ Done.
  `agent_server/prompts/loader.py` resolves
  `<catalog>.<schema>.prompt_<slug>@production` once at startup, with the code
  constant as a per-prompt fallback, and every call site now goes through
  `prompt_text(slug)`. It is **off unless `PROMPTS_FROM_REGISTRY` is set**:
  moving the source of truth for what the model receives is a behaviour change
  and belongs on dev before prod. Two properties worth knowing. Specialists are
  still built once and cached (`_get_specialists`), so this buys version pinning
  and prompt/trace linkage but *not* hot-swap — a prompt change needs a restart.
  And a loaded template is rejected in favour of the constant unless it is
  non-empty and still contains every variable its call site fills, because the
  registry stores Jinja `{{var}}` while the app formats with `{var}`: used
  unconverted, `str.format` leaves the placeholder in the prompt rather than
  raising.

  Two gaps found while doing it, both now closed. The `general` route's system
  prompt was built inline in `agent.py` and so was in no catalogue and no
  registry — the one route with no tools, and therefore the one most dependent
  on its prompt, was the one with no version behind it. And
  `governed_domain_prompt()` appends 734–792 characters at agent construction,
  roughly 15% of what a governed specialist actually receives, which existed
  only inside that function. Both are catalogued now, and
  `TestNoPromptIsBuiltOutsideThePackage` fails the build if a system prompt is
  ever again assembled from literals outside `agent_server/prompts/`.
- ~~**Register the agent as an MLflow LoggedModel.**~~ Done.
  `agent_server/logged_model.py` calls `set_active_model(name=...)` beside the
  `mlflow.langchain.autolog()` line, so traces attach to a model and the
  resolved model roster, prompt source and UC location are recorded as params.
  `set_active_model` creates the model on first boot and reuses it after, so no
  model id is threaded through the deploy — `create_external_model` turned out
  to be unnecessary. Every failure is swallowed: an app that will not start
  because it could not name a model is worse than unlinked traces.

  One thing left to decide. The name defaults to `prodline_copilot` for every
  deploy, which means one model accumulating all traces. Comparing quality
  across releases needs `AGENT_MODEL_VERSION` set at deploy time (the script
  already computes the git SHA for the prompt commit message) so each release
  gets its own model. Cheap to add; not added, because a name that changes on
  every deploy also fragments the trace history and that trade is worth making
  deliberately. Full pyfunc/`ChatAgent` packaging remains rejected — a served
  copy would need its own Genie spaces, Lakebase and warehouse wiring.
- **Split `start_server.py` (2770 lines) into FastAPI routers.** Pure refactor;
  defer until live deploy/smoke-test is available to validate route behavior.
  Impact: Med (maintainability). Effort: M.
- Real-data connector example (SCADA/MES via Lakeflow Connect) to complement
  synthetic mode. Impact: High. Effort: L.
- Configurable line topology beyond the fixed 19-machine line. Impact: Med.
  Effort: M.

---

## Backlog: patterns from the CO₂-EOR Digital Twin app

Reviewed 2026-08-06. A sibling Databricks App
(`production-optimizer-…databricksapps.com`, branded "Production Optimizer",
titled "CO₂-EOR Digital Twin") covers a different domain — enhanced oil
recovery in the Delaware Basin — but solves several presentation problems we
have not. Screenshots were taken at review time and deliberately stay out of
the repo — they are another team's product and this repo is publicly mirrored.
The 33-shot set is untracked in `.tmp_competitor/`, which means it survives
only until someone cleans the working tree; re-capture rather than assume.

- ~~**Demo Guide as a first-class in-app tab.**~~ **Done** —
  `client/src/components/DemoGuideTab.tsx`. Eight beats over the seven other
  tabs, each with a script written to be read aloud, a duration, "point at"
  bullets, product chips, and a button that opens the view being described.
  Two departures from their version. The counts are derived from the same
  catalogs the rest of the app renders from rather than typed in, because a
  guide is the one screen a presenter reads out loud and a stale number there
  is said to a customer rather than merely displayed. And it closes with the
  honest answers to the three questions this demo always provokes — does it
  write anywhere, is the data real, what is the ROI — since the fastest way to
  lose a technical room is to be caught overclaiming on the thing it already
  suspects.

  The prerequisite came with it: tab state now lives in the URL
  (`client/src/lib/tabRouting.ts`), so every view is linkable. It rides in the
  query string rather than the path because AppKit's `createApp` serves the
  client and its history fallback is not ours to guarantee, which is the
  difference between a deep link that survives a reload and one that 404s in
  front of an audience. The guide leads the tab bar; the line is still what
  opens by default, so the app does not greet an operator with instructions.

- ~~**A live schematic you can watch, not just read.**~~ **Done** —
  `client/src/components/ProcessFlowSchematic.tsx`, on the Line Status tab.
  Their fluid schematic draws the estate as one connected process with animated
  flow and per-equipment telemetry; ours drew three disconnected rows of tiles,
  so the line never read as a line. The new view lays the 19 stations out as a
  serpentine derived from `MACHINE_FLOW_ORDER`, which makes every consecutive
  pair adjacent and every row change a short vertical drop, and animates each
  section at its measured speed.

  It shipped first as a tab of its own beside Line Status, and that was the
  wrong shape: two pictures of one line, drawn from the same map, with the tile
  grid the weaker of the two and no reason for anyone to keep both true. It
  replaced the grid instead. Going the other way — keeping both — would have
  meant the stale one is the one someone eventually screenshots.

  Three decisions worth keeping. Motion is CSS `stroke-dashoffset` with the
  duration set per element, so ~18 connectors animate on the compositor rather
  than putting a repaint on the main thread every frame. Pace comes from
  `speed_pct` rather than `throughput_per_min`, because throughput is measured
  in different units along the line and animating 420 bottles a minute against
  17 cases a minute would make the front look twenty times faster for no
  physical reason. And the view finally distinguishes `blocked` from `starved`
  — both are real cascade statuses, `blocked` is absent from `STATUS_COLORS`
  and so renders as "idle" everywhere else in the app, and they point at
  opposite ends of the line for a diagnosis.

  It also closed a data gap rather than working around one: `feeds_machine_id`
  was emitted by the producer and stored in UC but dropped by the quality-sync
  job, the Lakebase inventory tables, and the SSE readers, so the app could say
  a material was low but not what would stop. It now travels the whole chain,
  pinned by `tests/agent_server/test_the_app_knows_what_a_material_feeds.py`.

  Still open, deliberately: the quantity standing between two stations. The
  cascade engine computes those buffer levels and drops them at emit, so no
  gauge in the view claims to know them. Publishing them is the next item.

- **Publish the buffer levels the cascade already computes.**
  `src/streaming_sim/cascade.py` maintains a `BufferState` between every
  adjacent pair — the thing that produces starved and blocked in the first
  place — and `snapshot_to_dicts` discards it, so the honest line schematic
  above can animate rate but not fill. Two columns on the machine row
  (`input_buffer_pct`, `output_buffer_pct`) would carry it without a new table
  or a new stream, following the same path
  `scripts/migrate_line_kpi_add_oee_columns.sql` took for the OEE columns:
  bronze DDL, the sync job's `select_cols`, the Lakebase DDL plus an idempotent
  `ADD COLUMN IF NOT EXISTS`, and the SSE reader. It would turn the schematic's
  connectors into real fill gauges and make "the buffer between the filler and
  the capper has 40 seconds left" a thing the app can say. Impact: Med-High.
  Effort: S-M.

- **Time-series charts.** We render none. Not one: no OEE across the shift, no
  sensor history on the machine detail card, no trend behind an incident. The
  only charts anywhere are inside the embedded Lakeview dashboard on the Data
  Quality tab. For a real-time monitoring product with sub-second telemetry
  that is the conspicuous gap, and the data is already in `telemetry_bronze`
  and `line_kpi_bronze`. (The EOR app has the same gap and it is just as
  noticeable there, which is how it got onto this list.) Impact: High.
  Effort: M. AppKit charts are already a dependency.

- **Scenario / what-if surface over the existing optimizers.** Their Scenario
  tab loads one AI recommendation, exposes three sliders, and returns a
  per-well predicted table with a plain-English footer stating the transfer
  function it used. Computed live server-side, ~110 ms. We have nine optimizers
  in `agent_server/optimizers/` — Monte Carlo forecasting, two-stage stochastic
  scheduling, MILP recovery, RUL — that only ever run when a specialist decides
  to call one. There is no way for a user to ask "what if the FILLER runs 10 %
  slower for the rest of the shift". This is the digital-twin capability, and
  it is mostly UI over machinery that already exists. Impact: High. Effort: M.
  Their version is worth studying for what to avoid as much as what to copy —
  see the failure notes below.

- **Specialist provenance up front, latency after.** Each of their five
  specialist cards names its backing resource *before it runs* (`Foundation
  Model API · claude-sonnet-4-5`, `UC SQL · <catalog>.<schema>.<table>`,
  `Vector Search · <index>`), shows an amber "running…" border, then flips
  green with an elapsed-time badge; the header carries the total. It makes a
  parallel fan-out legible in a way a spinner never does. We show the model
  badge on the answer and have the evidence trail, but nothing before or
  during, and no timings. Impact: Med. Effort: S.

- **Agent autonomy tiers.** Their API tags each agent `autonomous`,
  `supervised`, or `advisory`, and only `supervised` agents emit proposals
  needing human sign-off. That is a cleaner governance primitive than our
  current position, where every recommended action is a proposal because
  write-back does not exist. Tiering would let the distinction be a product
  decision rather than a limitation. Impact: Med. Effort: S.

- **Asset topology as a graph, with the analytics on the graph.** Their Field
  Overview draws the physical estate as a node-link network — wells, facilities,
  pipelines, CO₂ sources, monitoring points, fleet, flares — over a map, with a
  layer panel that toggles each class and carries its count, a right rail that
  inspects whatever you click, and an Ask Agent box scoped to the selection.
  Our line is already a graph and we draw it as a picture of one:
  `ProcessFlowSchematic` lays the 19 machines out as a connected path with live
  motion on it. That is still a picture rather than something you can compute
  over — which is the part still missing.
  The interesting version for us is not a map — we have one line, not a basin — but
  the analytics a graph makes available: cascade reachability (which machines
  a given fault can starve or block, and in what order), critical path and
  bottleneck ranking, and buffer capacity as edge weight. The cascade engine in
  the producer already computes propagation; nothing surfaces it as structure.
  Pairs naturally with per-node inspection, since clicking a machine already
  opens a detail card. Impact: Med-High. Effort: M.
  (Reference screenshot of their Field Overview kept out of the repo — it is
  another team's product and this repo is publicly mirrored. It is in the
  review set described below.)

- **An architecture view with live numbers on it.** Their Data & AI Flow tab is
  a three-swimlane diagram — Data Sources, Lakehouse inside a dashed Unity
  Catalog boundary, AI Agents & Serving — with animated edges, a legend,
  clickable node drawers, and **live row counts on the nodes**. We have the
  shape of this already in `SystemMap`, and we deliberately moved away from
  theirs: its docstring records that an earlier version drew surfaces →
  orchestrator → domains → foundation and was reshaped into a capability tree
  because the flow form "reads as plumbing". So this is not a new idea, it is a
  reversal to consider on new evidence. The evidence being the row counts: a
  diagram nobody can check is plumbing, and a diagram whose nodes report how
  many rows are in them is a claim the reviewer can audit live. Worth trying as
  an addition to the map rather than a replacement for it. Impact: Med.
  Effort: M.

- **A surface where the fan-out is the point.** Their Supervisor tab runs five
  specialists in parallel against one question and lands a synthesized verdict
  on top of the five cards. Ours routes to exactly one specialist, which is the
  better default — it is cheaper, faster, and the routing decision is itself
  the interesting engineering. But it means the compound-AI story is told and
  never shown. A deliberate fan-out mode, used sparingly, would demonstrate in
  one screen what the System Map currently has to assert. Note their version is
  also where their flagship bug lives (see below), so the failure mode is
  charted. Impact: Med. Effort: M.

### What their build got wrong, and what it warns us about

Two of these are failure modes we have already had, which is the reason to
record them.

- Their flagship Supervisor demo can never succeed: the recommendation
  dropdown's option `value` is the truncated *display label*, so the backend
  cannot resolve the id and every run returns DEFER with "resubmit with
  complete recommendation parameters".
- One of their own suggested Genie prompts returns "no recommendations
  available with annual revenue data" while the adjacent tab displays five
  totalling $2.5M/yr — `gold_recommendations` is empty and the optimizer
  computes at runtime. This is our five-dead-tables bug in another costume: an
  empty table returns zero rows rather than an error, and the surface stays
  confident.
- Their optimizer contradicts its own simulator — an action advertising
  +$1,159K/yr returns −$544K/yr when loaded into the scenario at defaults —
  and contradicts itself, recommending both "switch Apache to water injection"
  and "increase CO₂ injection on Apache". A what-if surface built on our
  optimizers has to be reconciled against the recommendation that seeds it, or
  it will do the same.
- Their physics is thin where it is most likely to be probed: choke position
  dominates every outcome with no decline, water-cut or GOR penalty, so the
  trivially optimal answer is always "open every choke". A domain expert finds
  that in about ninety seconds. Whatever we expose as a slider needs a
  penalty term on the axis a line manager would push hardest.
