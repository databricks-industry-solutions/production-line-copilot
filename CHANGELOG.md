# Changelog

All notable changes to ProdLine CoPilot are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.2] - 2026-08-04

A second security pass, this time over the repository itself rather than its
dependencies: static analysis of our own code, the CI workflows, and what the
public mirror discloses. Two real defects, both in the "safe until someone runs
the obvious command" category. No runtime code changed, so no redeploy is
required.

### Security
- **`app.prod.local.yaml` was the one operator config `.gitignore` missed.**
  The ignore list enumerated environments by hand — `app.local.yaml`,
  `app.dev.local.yaml` — while `render_app_yaml.py` writes
  `app.<env>.local.yaml` for whatever environment it is given. So the dev copy
  was ignored and the production copy, the one carrying the real Lakebase host,
  service principal UUIDs and resolved deployer email, was the single local
  config a `git add -A` would have staged. In a repository that is mirrored to
  a public solution accelerator, that is disclosure rather than mess. Replaced
  with an `app.*.local.yaml` glob, which also covers environments nobody has
  added yet. The committed `app.yaml` / `app.dev.yaml` SET_ME templates stay
  tracked, and a test asserts both halves: every environment the renderer
  supports is ignored, and the templates operators copy from are not.
- **The CI workflow ran `databricks/setup-cli@main`.** A branch ref re-resolves
  on every run, and that job holds `DEPLOY_NOTEBOOK_TOKEN`, so whatever landed
  on that branch would have executed in a workspace-authenticated context.
  Pinned to `v1.9.0`.

### Notes
- **Hardening still open: CI uses a self-hosted runner on `pull_request`.**
  `runs-on: html_publisher` combined with a `pull_request` trigger means a fork
  PR can execute on org infrastructure — the steps that run repository code are
  gated behind `vars.CI_DATABRICKS_HOST`, and GitHub's first-time-contributor
  approval stands in front, but neither is a boundary to rely on. Fork PRs
  receive no secrets, so the exposure is the runner, not the token. Moving the
  validate job to `ubuntu-latest` would close it; left alone here because it
  depends on how the org provisions runners.
- **What the scan found clean.** Bandit over 17k lines reports no HIGH
  findings. Of 59 MEDIUM, 54 are f-string SQL built from configured table names
  rather than request data; the rest are a dev-only `eval` already stripped of
  builtins, predictable `/tmp` paths in two setup scripts, and binding
  `0.0.0.0` in the server, which is the point. The client has no
  `dangerouslySetInnerHTML`, `innerHTML`, `eval` or `new Function`, and renders
  model output through `react-markdown` without `rehype-raw`, so HTML in an
  answer stays inert. `sql_query` gates on a `SELECT`/`WITH` prefix, denies
  nine mutating keywords anywhere in the statement, and runs against a
  read-only warehouse. Dev-only Python dependencies carry no advisories.
- **`ruff` reports 36 findings and no CI job runs it.** All pre-date this
  release and none are security-relevant, but a linter nothing enforces will
  keep drifting.

## [0.3.1] - 2026-08-04

Dependency security patches. Prompted by four Dependabot PRs on the public
mirror, but an OSV scan of all 197 runtime packages and 938 npm packages found
that Dependabot had flagged two of the four vulnerable Python packages and
missed two others, so the scan — not the PR list — is what this release acts on.

### Security
- **aiohttp 3.14.1 → 3.14.3.** Three advisories, worst HIGH: an out-of-bounds
  heap read in the C HTTP response parser on a malformed chunked response
  (CVE-2026-69244), request smuggling via WebSocket upgrade (CVE-2026-69243),
  and a WebSocket client accepting compressed frames without negotiated
  permessage-deflate (CVE-2026-59881). Transitive through `mlflow`, `litellm`,
  `langchain-community` and `unitycatalog-client`, all of which make
  authenticated calls from the app runtime.
- **GitPython 3.1.53 → 3.1.57.** Seven advisories, five HIGH, all variants of
  argument injection through unguarded git option forwarding — arbitrary file
  overwrite via `IndexFile.checkout()` and `--output` on `diff`/`rev-list`,
  arbitrary command execution via `--template` on clone, and environment
  exfiltration via `create_remote()` URLs. Transitive through `mlflow-skinny`;
  we never import it, and nothing routes untrusted input to git, so the
  reachable risk here was low and the fix is free.

### Notes
- **Two vulnerable packages cannot be fixed yet, and neither had a Dependabot
  PR.** `cryptography` 48.0.1 carries three advisories (two HIGH), fixed in
  49.0.0 and 50.0.0, but mlflow 3.13–3.14 requires `cryptography>=43,<49`, so
  the upgrade is unsatisfiable until mlflow relaxes the cap — and 50.0.0, which
  the HIGH Bleichenbacher-oracle fix needs, is not published on our index at
  all. `protobuf` has a HIGH JSON-recursion bypass (CVE-2026-0994) fixed in
  6.33.5; the lock already resolves to 6.33.6 on Python ≥3.12 and only falls
  back to the vulnerable 6.31.1 on 3.11, because 6.33.x drops 3.11 support.
  Raising `requires-python` to `>=3.12` would close it and is deliberately not
  done in a patch release.
- **The two npm advisories are deferred, not dismissed.** `brace-expansion`
  5.0.8 (HIGH, DoS) and `postcss` 8.5.19 (MODERATE, arbitrary `.map` read) both
  arrive through build tooling only — vite, tailwind, autoprefixer and eslint —
  so neither ships in the browser bundle or runs in the deployed app. They were
  left at their current versions because `registry.npmjs.org` is unreachable
  from the machine this release was cut on, and the alternative was writing
  integrity hashes sourced from a third-party mirror into `package-lock.json`.
- **`npm audit` reported zero vulnerabilities and was wrong.** Its advisory
  endpoint was failing, and a failed bulk request is reported as a clean result
  rather than an error. Both npm findings above came from OSV instead. Worth
  remembering the next time a green audit is taken as evidence.

## [0.3.0] - 2026-08-04

Three themes. Every agent role now runs on the model that suits its work — seven
vendors behind one Foundation Model API — the governed manufacturing model is
split into four per-domain Genie spaces, one per schema, with each specialist
handed only the domain it works in, and that governed model itself arrives here:
the Manufacturing Industry Data Model silver layer with opt-in DQX quality
checks, wired into the bundle so it installs, populates and refreshes on its own.
The About tab gains a System Map that draws the wiring this deployment actually
has, and the chat shows the evidence a tool left rather than the model's account
of itself.

Supersedes the 0.3.0 entry, which was written but never tagged or published; its
work is folded in below, so the public accelerator goes 0.2.1 → 0.3.0 with no
gap.

### Added

- **Per-domain governed Genie spaces.** The governed manufacturing model is
  organised into four schemas — `asset`, `quality`, `inventory`, `production` —
  and each now gets its own curated Genie space, with each specialist handed
  only the domain it works in: asset to Maintenance Predictor and Downtime
  Analyst, quality to the Quality Specialist, inventory to Supply Chain, and
  production to OEE Coach, Schedule Optimizer and Strategic Advisor. The split
  follows the model's own schemas rather than the agent list, so the boundary is
  a Unity Catalog schema and per-domain grants are real. Genie accuracy is a
  function of scope: two well-described tables answer far more reliably than
  twelve. `scripts/genie_space_config_mvm.py --domain <name>` renders each space
  from the same source of truth as the combined one, carrying only the
  instructions, join rules and examples that apply inside the domain.
  These tools AUGMENT `sql_query` rather than replacing it — Genie is a
  poll-based round trip, so the fixed analytical paths stay on direct SQL, and
  the prompts say so. A domain whose space is unprovisioned yields no tool at
  all, so the specialist degrades to `sql_query` instead of being handed one
  that always errors.
- **Strategic Advisor now knows the whole governed model exists.** It owns a
  Genie space over the `production` domain only, and Genie cannot join across
  spaces, so cross-domain trends — reliability against output, defects against a
  supplier's material, PM adherence against downtime — have exactly one route:
  `sql_query` across the four governed schemas, which share a catalog and so can
  be joined in a single query. Its prompt never mentioned the model, making that
  data unreachable by either path on the one specialist whose job is
  cross-domain reasoning. Schedule Optimizer remains deliberately excluded: it
  is the specialist a user reaches mid-disruption, its constraints already
  arrive deterministically from `line_constraints`, and its prompt caps it at
  three tool calls.
- **The System Map now leads the README too.** The front page had a pipeline
  diagram and no picture of the product, so a reader met Zerobus before they met
  the two branches. `scripts/dev/capture_system_map.mjs` screenshots the real
  `SystemMap` component — not a redrawing of it — in both colour schemes, with
  the component fetching its wiring from fixtures captured off a live
  deployment, so the README shows four provisioned domains and the models that
  actually answered. A Mermaid redrawing was tried first and dropped: it would
  have been a second, hand-maintained copy of a component that already exists.
  The fixtures are pinned to `DOMAIN_ENV` and `_DOMAIN_OWNERS`, so adding a
  domain or moving a specialist fails the suite instead of quietly leaving a
  stale picture on the front page.
- **`GET /ops/topology`** and a **System Map** leading the About tab: how a
  question travels from a surface, through the router, to the governed domain
  that answers it. Domain wiring is read from the server, so a space that did
  not provision is drawn as absent rather than asserted. Each specialist card
  shows what it carries in three categories — its governed Genie space, its
  deterministic solvers, and its plain functions — rather than a single "Genie"
  badge, which both under-described a specialist carrying five to seven tools
  and duplicated the domain named beside it. The three specialists that also
  reach the **line ops** space now say so. That space is named for its scope
  rather than called "live", because liveness is not what separates those three
  — all eight read the live tables with direct SQL — and setting "live" against
  "governed" framed two scopes of one thing as two kinds of thing. It is also
  the largest space in the project, 14 curated tables against 12 across all four
  governed domains, so the map now says why it is broad where a governed domain
  is narrow: the split follows grant boundaries, and the live schema has none.
- **Evidence trail in the chat.** "How this decision was made" now shows what
  each tool actually did, not just that it ran: the governed domain that
  answered, the tables touched, the row count, and the query itself — Genie's
  generated SQL, or the SQL the model wrote for `sql_query`. All of it already
  existed and was dropped at the agent boundary. Row counts are honest about
  truncation: both tools cap their results (20 rows for Genie, 500 for
  `sql_query`), so a count that lands on the cap renders as `20+` rather than
  being presented as the size of the result set.
  We deliberately do *not* surface model reasoning blocks, which stay dropped in
  `llm_output`. Stated reasoning is frequently unfaithful to what the model
  actually computed, so it manufactures confidence where a re-runnable query
  earns it; OpenAI's gpt-oss guidance separately warns that raw CoT can leak
  system-prompt content and contradict the answer it precedes. Only two of the
  ten roster models emit reasoning at all, so an evidence trail is also the only
  confidence surface that reads the same whichever vendor answered.

- **Per-role multi-vendor model roster** (`agent_server/model_roster.py`). Every
  agent role now runs on a model chosen for the shape of its work instead of
  sharing one of two OpenAI endpoints: root-cause analysis on Claude Sonnet 5,
  strategy on Claude Opus 5, OEE decomposition on GPT-5.4, quality risk on
  GLM-5.2, RUL trending on Qwen 3.5 122B, supply chain on Llama 4 Maverick,
  schedule narration on the open-weights GPT-OSS 120B, briefings on Claude
  Haiku 4.5, and intent routing on Gemma 3 12B. Six vendors behind one
  Foundation Model API, one permission model and one bill.
- **Availability fallback.** A role whose preferred model isn't served in the
  workspace degrades to its tier endpoint (`MODEL_SPECIALIST` /
  `MODEL_ADVANCED`) and logs the substitution, so partial vendor coverage
  deploys cleanly instead of 404-ing. `MODEL_MULTI_VENDOR=0` restores the old
  two-tier behaviour; `MODEL_<ROLE>` pins any single role.
- **A Multi-AI tab** carrying the vendor story on its own surface: which
  vendor holds which seat and why that model suits that work, the live roster
  from `GET /ops/models` including any role that fell back, and the engineering
  notes behind the choices. Every vendor pill carries the Databricks mark,
  because the point is that seven vendors run on one platform, one permission
  model and one bill — not that the app talks to seven APIs.
- **`GET /ops/models`** and a live Model Roster table in the About tab, showing
  what actually answers here — including any role that fell back — plus a
  vendor badge on every chat answer.
- Preflight now WARNs (never fails) per role when a preferred model is missing,
  naming the roles that will degrade.
- **Model choices are config, not code.** A `model_roles` map in
  `config/project-settings.yaml` pins any subset of roles to specific endpoints
  (roles you omit keep the roster default); the renderer emits them as
  `MODEL_<ROLE>` env vars, drops entries you remove, and rejects unknown role
  names rather than writing a variable nothing reads.
- **`preflight.py <env> --models`** prints a per-role coverage table — role,
  endpoint, whether it came from config or the shipped default, and whether
  this workspace serves it. It also runs on every `deploy.sh`, so partial vendor
  coverage is visible at deploy time instead of in the About tab later. Never
  fatal.
- **Every answer links to the MLflow trace that produced it**
  (`agent_server/trace_link.py`). "How this decision was made" explains the run
  in prose; the link under it opens the run itself in the workspace MLflow UI.
  Only the trace id is read, which is available the instant the run ends, so the
  link costs nothing on the response path. It sits in the collapsed expander
  rather than on the message because it needs workspace access, and it is
  omitted entirely when the server has no experiment to point at, so a local run
  shows no dead link. The lookup is thread-scoped: MLflow's default is
  process-wide, which on a pool of six specialist workers would link one user's
  answer to another user's trace. What the page shows depends on where the app
  runs. Everywhere the artifact store is reachable it is the whole run — spans,
  prompts, tool calls, tokens, per-step latency. From Databricks Apps it is the
  summary only: question, specialist, status, total duration. The spans are the
  part that uploads to blob storage, and that upload is the known egress
  limitation below.

*The governed data layer below was developed as 0.3.0 and never tagged, so it
ships here.*

- **Industry Data Model (MVM) install** (ID1): `scripts/install_industry_data_model.py`
  pulls the Manufacturing MVM model SQL from upstream and applies it via the SDK
  Statement Execution API (serverless-safe), wired into the one-time setup job.
  Idempotent; `_ensure_catalog` reuses an owned catalog when metastore
  `CREATE CATALOG` is denied.
- **Silver mapper** (ID3): `agent_server/serving/silver_mapper.py` maps streaming
  bronze into the governed MVM silver layer (asset / production / quality /
  inventory). Seeds all targets once at setup (`populate_silver_model`).
- **Streaming silver** (design B): the `streaming_quality_sync` job MERGE-upserts
  each micro-batch into the MVM FACT tables (`asset.failure_record`,
  `inventory.stock_balance`, `production.production_line`,
  `quality.inspection_result`) from inside `foreachBatch` (`--mvm-silver`, opt-in
  via `mvm_silver_enabled`). The governed model now rides the same Zerobus→bronze
  streaming path at seconds latency instead of a separate batch — collapsing three
  data paths back to two. Adds a `quality_checks` stream for inspection results.
  Non-fatal and deploys dark, like DQX. Slow-changing dimensions are still seeded
  once at setup.
- **DQX data-quality augmentation** (EQ2): opt-in (`dqx_enabled`) freshness /
  range / referential checks in the `streaming_quality_sync` job (DQX >=0.15),
  appending flagged rows to a normalized `dqx_warnings` audit table that feeds the
  Data Quality dashboard. Observability-only — never affects the valid/reject
  path — and deploys dark (default OFF).
- Per-profile `mvm_catalog`, `dqx_enabled`, and `mvm_silver_enabled` settings
  (fall back to the live catalog / OFF).
- Reference notebooks `06_Install_Industry_Data_Model` and
  `07_Populate_Silver_Model` mirroring the new setup tasks.
- **Governed-model grounding** (ID4): a SEPARATE, curated Genie space over the 12
  populated MVM entities (`scripts/genie_space_config_mvm.py`) for standards-aligned
  / audit-style questions, kept distinct from the tuned live mobile-briefing space.
  It describes ONLY the columns the silver mapper actually populates (a test gate
  cross-checks this against `silver_mapper.MAPPINGS`). `deploy.sh` auto-creates /
  refreshes it and grants the app SP `CAN_RUN` when `mvm_silver_enabled` is on;
  the id is stored per-profile as `mvm_genie_space_id`. The SQL specialists
  (downtime, quality, maintenance, supply-chain, OEE) gain a shared
  `GOVERNED_MODEL_NOTE` so they can query the governed model on demand while
  still defaulting to the live streaming tables for real-time work.
- **Reconciliation tab** (`/ops/reconciliation`): proves the live plane
  (Lakebase) and the analytics plane (Zerobus → bronze → QC gate → gold →
  governed MVM) carry the same truth, instead of asking users to take it on
  faith. Per-stream counts across all four layers, with deviation split into
  **explained** (rows the QC gate rejected, recorded in `quality_rejects`) and
  **unexplained** — the only number that goes red. Also surfaces DQX warnings
  ("flagged but kept"), governed-model population and freshness, and an OEE
  *value* spot-check across the two paths, which is what catches mapping bugs
  that matching row counts cannot. Counts are taken over a settled window
  (ending ~2 min back) so normal streaming lag never reads as data loss, and
  both engines receive identical absolute bounds so a boundary row can't drift
  a tick between them. Upsert-style governed targets are reported by freshness
  rather than count parity, and incident tables at zero read as
  "empty (expected)".

### Changed

- **The System Map is a capability tree, not a data-flow diagram.** The first
  version drew surfaces → orchestrator → domains → foundation, which reads as
  plumbing and buried the fact reviewers react to. It now opens on the app and
  splits into the two things it does: a **Control Tower** of streamed
  operational views with no model in the path, and a **Specialist Copilot**
  where a router hands a question to the one specialist that owns it. There is
  no third "always-on agents" branch, because nothing here runs without a user
  asking, and drawing one would be a claim rather than a description.
- **The About tab's pipeline reads as two tiers, because there are two.** The
  governed-model MERGE and the QC gate are sinks of the Tier-2 streaming job,
  not tiers of their own; sibling headings made them look like four and invited
  the question every time.

*The governed data layer below was developed as 0.3.0 and never tagged, so it
ships here.*

- **Data Quality and Reconciliation merged into one "Data Integrity" tab**: both
  answered the same question — can I trust these numbers — and as sibling tabs a
  visitor picked one without knowing it was half the answer. The cross-path
  verdict now runs full width above a two-column split: the quality-gate
  dashboard keeps the wider side (the embedded Lakeview iframe doesn't reflow
  into a narrow column), and the reconciliation detail sits beside it, rewritten
  for that width — each stream is a two-line row with a `live → bronze → gold`
  chain rather than an eight-column table, and the long-form explanations moved
  into disclosures. Stacks to one column below 1280px instead of cramming both.
- Slimmed the README architecture section to a compact mermaid diagram; the full
  reference stays in `docs/ARCHITECTURE.md`.
- Pinned `databricks-labs-dqx>=0.15` (0.15 changed the `DQEngine` constructor and
  the freshness check name vs 0.10).
- Retired the 30-min `silver_model_refresh` batch job; MVM fact tables are now
  kept fresh by the streaming job's per-micro-batch MERGE (design B). A stale copy
  is dropped from the workspace on the next `databricks bundle deploy`.

### Removed

- Dead code, ahead of the public release: the unused `agent_server/hitl/`
  subsystem and its two write-only UC tables (`decision_log`, `draft_artifacts` —
  the decision log is client-side), the orphaned `scripts/genie_space_config.py`
  (which described three tables a standard deploy never creates),
  `_make_partition_writer` and the four `*_HISTORY` SQL constants in the
  streaming job (built at import and immediately discarded — the producer owns
  that sink), and four unreferenced scripts/fixtures.

### Fixed

- **Ten of the seventeen tool labels read as broken English.** The ✓ line under
  a finished tool call was built by string replacement — `"ing " → "ed "`, then
  `"Running " → "Ran "` — and the first substitution consumed the prefix the
  second was looking for, so the chat said "Runned anomaly detection" and
  "Queryed production database". "Estimating remaining useful life" became
  "Estimated remained useful life", a blanket replace having no way to tell a
  verb from the middle of a noun phrase. Conjugation is now a table of verbs; a
  label whose verb is not in it fails the suite rather than the demo.
- **One NULL could take down a whole answer.** Both live readers `SELECT *`, so
  a nullable column arrives as a present key holding `None` — which is why the
  `.get(key, 0)` defaults in the shift snapshot and the `setdefault` in the KPI
  normaliser both looked like they covered this and neither did. `float(None)`
  raised out of `_quick_snapshot`, which runs unguarded on the request path, so
  a single null availability reading returned a raw Python `TypeError` to the
  user instead of an answer. Null and absent now mean the same thing, resolved
  once at the reader.
- **The evidence trail could attach a query to the wrong step.** A specialist
  calling one tool twice in a turn produced two steps with the same label, and
  both results landed on the second: the first kept its label and lost its
  query, the second showed one call's SQL beside another call's row count. A
  trail that exists to be checked is worse wrong than absent, so evidence now
  fills its own step in order.
- **A finished answer could be discarded as a failure.** The stream consumer
  breaks out on an empty queue if the worker has exited, and the worker enqueues
  its result immediately before exiting — so a specialist that completed inside
  that one-second window had its answer dropped and the user saw "Analysis could
  not be completed" beside a working trace link to the run that had, in fact,
  completed. The drain now reads the result it finds.
- **The live overlay wrote into the cached context bundle.** `build_context`
  hands out a shallow copy, so the nested `scenario` dict is the object the
  cache holds; stamping it as live left it that way for the rest of the TTL. The
  next request served from that entry with Lakebase down then took the early
  return — on the documented promise of leaving the historical bundle untouched
  — and gave the specialist historical readings under a live scenario name and a
  current timestamp, which is the half-overlaid state the fallback exists to
  avoid.
- **Preflight validated only the tiers someone had configured.** It returned the
  configured endpoints *instead of* the shipped defaults, so filling in one
  field switched off checking of the others: a profile naming only the
  specialist tier left `databricks-gpt-5` unverified, which is what the advanced
  roles actually resolve to. The deploy passed clean and every advanced role
  then failed on its first model call. Both are checked now, and the defaults
  are read from the roster rather than restated.
- **`render_app_yaml` could drop every new variable in silence.** A trailing
  comment on the `env:` line made the block unfindable, and the function
  returned "nothing added" — indistinguishable from "nothing was missing", which
  is the exact silent no-op it was written to prevent. It now stops the deploy
  instead. A comment on a `- name:` line had the mirror problem: the key looked
  absent and was appended a second time, leaving two entries for one name.
- **The route guard had stopped covering the routes it names.** It looked for
  `fetch('/api/...')` literals, so the three paths added this release were
  invisible to it: two go through the shared ops helper and one through
  `EventSource`. One of them is `/api/ops/models`, the unproxied path the test
  was written for. It now reads any `/api/...` literal, with the awkward ones
  named individually so the extractor cannot quietly stop finding them again.
- **Counts stated in prose disagreed with the counts beside them.** The System
  Map's card read "7 deterministic optimizers" above a sentence naming six; the
  About tab said "five curated spaces" two hundred lines above a paragraph
  counting them from the topology endpoint, and "six vendors" directly below a
  stat reading the roster. All of them now derive from the same source as the
  number next to them.
- **Two tabs could take the whole app down.** The Multi-AI and About tabs
  rendered server JSON without an error boundary, so a shape the client did not
  expect was caught only at the root — replacing the live line status along with
  the tab that failed. Both are wrapped, and the roster helpers tolerate a
  missing `roles` array.
- **`fetchOpsJson` threw on a JSON body of `null`.** `JSON.parse("null")`
  succeeds, so reading `.error` off the result raised a `TypeError` from inside
  the helper written to keep parser noise away from users.
- **The About tab fetched its two ops endpoints twice per visit.** It and the
  System Map each called them, and the tab remounts on every switch. The map now
  takes them as props, and surfaces a roster failure instead of silently
  dropping every model name.
- **A logging bug was masking, and worsening, the trace-export failure**
  (`agent_server/sql_logging_compat.py`). Two libraries that are each behaving
  correctly combined into one that is not. MLflow and `databricks-sql-connector` both
  attach a redaction filter to `urllib3.connectionpool`, a logger neither owns.
  MLflow's runs first and, when it rewrites a message containing credentials,
  clears the arguments it has just consumed — legal, and documented. The
  connector's runs next and iterates `record.args` unconditionally, so it raises
  `TypeError`; a filter raises inside `Logger.handle`, which puts the exception
  on the caller. The caller is MLflow: span data uploads to blob storage over
  `requests`, urllib3 logs a retry warning carrying the presigned URL, MLflow
  redacts it and nulls the args, and the retry dies. So the crash could only
  happen on the retry path, where MLflow was already recovering from something
  else — and it converted that recoverable failure into a `TypeError`, reported
  as `'NoneType' object is not iterable`, which named neither the real cause nor
  the library at fault. Removing it revealed what had been underneath since
  0.2.0: the Apps runtime cannot reach the artifact storage host, so trace
  metadata is written and the spans never upload (see Known issues). Local
  probes exported cleanly every time because they never retried. The
  connector's filter is now wrapped to tolerate argless records — on the class,
  since its instance is created at connector import and, as a first attempt
  established, guarding the head of the filter chain achieves nothing when the
  `None` appears halfway down it. Disables via `SQL_LOGGING_COMPAT=0`. The tests
  reproduce the failure through the real logger and pin both halves, so the
  release that fixes either library fails the suite and the shim can go; drafted
  upstream in `docs/upstream-issues/`.
- **The inline MLflow span viewer had been dead since the MLflow 3 upgrade, and
  said nothing.** It called `mlflow.get_last_active_trace()`, which MLflow 3
  removed in favour of `get_last_active_trace_id()`. The resulting
  `AttributeError` was swallowed by a bare `except` that logged at debug level,
  so the capture returned an empty list on every answer, every answer shipped
  `trace_spans: []`, and the Decision Log's viewer never rendered — for a whole
  release, with nobody noticing. The viewer is now removed rather than repaired:
  reading spans back in-process needs `get_trace(id, flush=True)`, which blocks
  on the async export queue — about a second per answer — to show a truncated
  copy of what the MLflow UI renders in full. The link above replaces it, and
  `tests/agent_server/test_trace_link.py` fails loudly if MLflow moves the call
  again.
- **Two vendors were dark and nothing said so**
  (`agent_server/tool_schema_compat.py`). `calculator` takes an open `params`
  dict because it reads different keys per operation, and pydantic renders that
  as `additionalProperties: true`. Qwen 3.5 122B and Llama 4 Maverick reject any
  request carrying the keyword; the roster's other eight endpoints accept it.
  Since almost every specialist binds `calculator`, Maintenance Predictor and
  Supply Chain Advisor failed before the first token and fell through to the
  general handler with "encountered an error" — so two of the seven vendors the
  multi-model story rests on had never actually answered. The keyword is now
  stripped at bind time, which is a no-op in JSON Schema terms because an absent
  `additionalProperties` already means the same thing; a literal `false` and a
  schema-valued one are left alone. Disables via `LLM_TOOL_SCHEMA_COMPAT=0`. A
  guard test fails if any shipped tool would reach a model with an open dict,
  and paired upstream-contract tests fail if pydantic stops emitting the keyword
  or the bind seam moves.
- **The Quality Specialist ran out of steps before it ran out of ideas.** Both
  agent paths capped a ReAct run at 10 LangGraph super-steps, which buys four
  rounds of tool use. That was already tight and the governed domain tool spent
  one of them: a run that asks its domain, queries live SQL, and then has to
  retry after naming a column that doesn't exist needs a fifth, and the deployed
  app failed two runs in three with `GraphRecursionError`. The budget is now one
  named `SPECIALIST_RECURSION_LIMIT` (default 16, seven rounds) shared by the
  streaming and non-streaming paths, which each carried their own literal 90
  lines apart. Wall clock was always the real ceiling — `SPECIALIST_TIMEOUT_SECONDS`
  is unchanged at 90s — so a looping specialist is still bounded.
- **The Quality Specialist was guessing at the live table's columns.**
  `sql_query` advertises no schema, so a model that wants a reject rate invents
  one — observed against the deployed app: `defect_type`, `reject_count`,
  `total_checked`, `reject_rate`, none of which exist on `quality_checks`. The
  failed query cost a full ReAct round, which is most of why this specialist ran
  56–93s. Its prompt now names the nine real columns and says plainly that a rate
  has to be derived from `result`. A test reads the DDL and fails if the prompt
  omits a column, invents one, or stops warning about the four it hallucinated.
- **A third simultaneous question got a 502.** The specialist thread pool ran two
  workers, so a third asker queued behind two runs that can each last 90s and the
  gateway gave up first — which is what a room asking at once looks like. Now six,
  overridable with `SPECIALIST_WORKERS`. These threads block on the serving
  endpoint and the warehouse rather than on CPU.
- **Every worker was running SQL down one shared connection.**
  `databricks-sql-connector` declares PEP 249 `threadsafety = 1` — threads may
  share the module, but not connections — and the tool held a single
  module-level connection, locked only while handing it out. So concurrent
  statements interleaved on one Thrift transport, and worse, a failure in one
  thread closed the connection every other thread was mid-query on, failing
  those too, each of which then closed it again. Widening the pool from two
  workers to six turned a narrow race into a likely one. Connections are now
  per-thread, which removes the sharing rather than locking around it —
  serialising would have handed back exactly the concurrency the pool was
  widened for — and takes the health check and the connection handshake out of
  a global critical section. A test fails if the driver ever relaxes its
  threadsafety declaration and makes this unnecessary.
- The four `governed_*_query` tools were missing from the tool-label map, so the
  live trace showed a raw function name — "Using governed_quality_query" —
  mid-answer. A test now fails if a governed domain is added without a label.
- **Preflight said nothing about the governed domains in the one state that
  matters.** `if wired:` skipped the empty list, so an environment where
  provisioning had never run reported exactly what a fully wired one did:
  nothing. Whether that is news depends on `mvm_silver_enabled` — with it off
  there is no governed model to point a space at and SQL-only specialists are
  the design, with it on, zero wired means every domain specialist is quietly
  degraded — and the two now read differently. The same check also referenced
  `Status.OK`, which is not a member of that enum, so the fully-wired branch
  would have raised `AttributeError` on the next deploy of any environment that
  had captured its space ids. Both found by writing the check's first test.
- **The About tab claimed a wiring the deployment might not have.** The headline
  hardcoded "5 Genie spaces" directly above a System Map that counts them from
  live topology, so the same screen could state two different numbers; the count
  now comes from the same source and pluralises, because it reads "1 Genie
  space" in exactly the deployment nobody rehearses on. Specialist tiles read
  "asset not provisioned" beneath a paragraph claiming each specialist reaches
  its own governed domain, which together made a supported configuration look
  broken — the chip now says what the specialist has, "sql only", and the
  paragraph has a variant per state, each ending on the live-SQL path that is
  true everywhere.
- **Two more places on the About tab drew a governed model the deployment did
  not have.** The pipeline diagram drew the governed-model sink as running
  regardless of `mvm_silver_enabled`, so an environment with no governed model
  showed one flowing; it is now dimmed and carries the reason. And in the Agent
  Architecture list each specialist's domain badge was struck through when its
  space was unprovisioned. Production runs without the governed model by design,
  so all four domains come back unconfigured and every specialist wore a
  crossed-out badge — the same "supported configuration reads as broken" fault
  the System Map had already been fixed for, one section above on the same page.
  The badge now gives the System Map's answer, "sql only": what the specialist
  has, not what it lacks. Both were found by looking at the About tab in the
  configuration production actually runs, which is the one no local preview
  defaults to.

- **Vendor fields on tool calls no longer die between turns**
  (`agent_server/vendor_compat.py`). `databricks-langchain` rebuilds each tool
  call from a four-field whitelist when sending an assistant message back, which
  is lossless for OpenAI and Anthropic and lossy for Google. The shim copies the
  dropped keys back by tool-call id, is a no-op for vendors that carry none, and
  disables via `LLM_VENDOR_COMPAT=0`. Because it patches a private function, its
  tests assert the upstream shape and fail loudly if a release renames the seam
  or fixes the bug.
- **Reasoning models leaked their scratchpad into the chat.** GPT-OSS and some
  Qwen builds return `content` as a JSON-serialised list of typed blocks with
  the model's private reasoning in front of the answer, and `ChatDatabricks`
  hands that over as a *string*, so the usual `isinstance(str)` guard missed
  it. All six content reads in `agent.py` now go through
  `agent_server/llm_output.py`, which drops reasoning blocks and returns the
  text. Observed 4/4 runs on GPT-OSS 120B and 1/4 on Qwen 3.5 122B before the
  fix.
- **Intent routing would have silently collapsed to "general"** on any model
  returning block content: `_classify_intent` called `.strip()` on it, raised,
  and was swallowed by the bare `except`. Same normaliser, plus a regression
  test.
- **`MODEL_MAP` could drift from the model that actually ran.** It was a
  hand-maintained copy of the tier rules, harmless while every specialist
  shared an endpoint and wrong as soon as they didn't; it now resolves through
  the same function the factories use, lazily so importing `agent.py` still
  costs no workspace round-trip.
- `render_app_yaml.py` now removes a stale `MODEL_ORCHESTRATOR` from an existing
  `app.<env>.local.yaml`. It used to be defaulted to the specialist endpoint, so
  leaving it in place would have pinned both routing and synthesis to one model
  and quietly overridden the roster on exactly the longest-running deployments.
- **The model roster never loaded in the browser.** The Express server proxies
  `/api/*` to the Python backend route by route, and `/api/ops/models` had no
  route, so the client got Express's HTML 404 and `res.json()` failed on it —
  surfacing as `Unexpected token '<'`, which describes the parser's day rather
  than the problem. The route exists, the client reports an unregistered route
  as one, and a test walks every `/api/` path the client fetches and fails if
  the server does not answer it.
- **The Databricks mark was upside down.** The hand-drawn path in the shared
  icon was vertically flipped against the real logo, on a tab whose whole point
  is that these vendors run on Databricks. Replaced with the official CC0
  geometry, pinned by a test, and sized up slightly because the official mark is
  an outline and 10px was mush.

*The governed data layer below was developed as 0.3.0 and never tagged, so it
ships here.*

- **One bad Lakebase read broke every other request**: the live plane runs on a
  single process-wide connection with `autocommit = False`, so a failing
  statement left an ABORTED transaction and Postgres rejected everything after
  it with `InFailedSqlTransaction` — one panel's error became the whole
  dashboard's. Reads now go through `pg_pool.query`, which rolls back on error,
  reconnects and retries once when the connection itself dies (token expiry,
  pooler recycling), and reports unreachable Lakebase as no data rather than
  raising.
- **A Lakebase outage stalled the entire app, not just the live panels**: the
  SSE generator made six blocking `psycopg2` calls per tick directly on the
  FastAPI event loop, where a connect timeout freezes every concurrent request.
  Measured at 2.4 s of loop stall per tick with slow reads; the tick's reads now
  run in one worker-thread hop and loop lag stays in the milliseconds.
- **The circuit breaker never actually tripped**: `_retry_count` was declared
  and compared against a maximum but never incremented, so during an outage
  every single call paid two connect timeouts plus an OAuth token mint. Replaced
  with a breaker that opens after three consecutive connect failures, backs off
  5 s → 120 s, and resets the moment Lakebase answers.
- **Failed table init silently changed transaction semantics**: `_init_tables`
  flips `autocommit` on to run DDL and restored it only on the success path — so
  the routine failure (no `CREATE` grant for the app service principal) left the
  shared connection in autocommit mode for the rest of the process's life.
- Recovering the shared connection no longer costs a reconnect when a rollback
  will do: the health probe now retries once after rolling back.
- **A failing read could silently discard a write that was in flight**: reads
  moved to worker threads while writes (chat history, scenario commands, demo
  reset) still ran unsynchronised on the same shared connection — and therefore
  in the same transaction. A read landing between a writer's INSERT and its
  COMMIT would roll that INSERT back on failure, with no error anywhere: the
  writer's `commit()` then succeeded having committed nothing. Writes now go
  through `pg_pool.transaction`, which holds the pool lock for the whole unit;
  the connection-lifecycle and statement locks were merged into one reentrant
  lock, which also removes a lock-ordering deadlock between them.
- `/sim/commands` returned 500 for as long as any incident was active: every row
  carries a `created_at` and Starlette's JSON encoder cannot render a datetime.
- **A Lakebase outage still froze the app through five other endpoints**:
  `/sim/status`, `/sim/commands`, `/sim/warmup`, `/sim/resolve` and
  `/sim/recover_machine` are `async` but called blocking psycopg2 directly, so
  they stalled the event loop exactly as the SSE generator used to.
  `recover_machine` also ran one serial UPDATE per open command on the loop.
  All now hop to a worker thread.
- Reconciliation no longer paints an unverified zero green. When a hop can't be
  counted the backend reports `unknown` with zero unexplained rows — the panel
  rendered that as a passing check, which is the exact failure it exists to
  catch. It now reads "not verified".
- `_restart_demo_state` reported per-table delete counts that a later failure
  had rolled back; each table now clears inside its own savepoint.
- A test guards `uv.lock` against private index URLs, which a `uv run` on a
  corporate network silently rewrites into it — leaving anyone outside that
  network unable to install the project.
- **Teardown could destroy another environment's governed model**: the MVM
  schemas (`asset`/`production`/`quality`/`inventory`) are catalog-level and
  carry no environment suffix, so every profile pointing at the same
  `mvm_catalog` shares one physical model. `cleanup.sh prod --purge-data` would
  have dropped the schemas dev was streaming into — cross-environment data loss
  from a command whose target says "prod". The teardown now resolves which other
  profiles share the catalog and skips the governed purge when it isn't the sole
  owner, naming what it protected.
- **Reconciliation presented another environment's governed rows as its own**:
  with the governed model shared, an environment whose streaming-silver writer
  is off still read the other's tables. Prod reported an OEE spot-check of 74.3
  live vs 74.4 governed — every row written by dev — which read as a passing
  cross-plane check on an environment that writes no governed rows at all.
  `MVM_SILVER_ENABLED` now reaches the app; when this environment doesn't write
  the model, those queries are skipped entirely and the OEE verdict is withheld.
- **A stale seed catalog failed 15 minutes into setup**: `src_catalog` is
  optional (empty generates reference data in-workspace), but when set the setup
  job copies from it, and the failure landed in `bootstrap_reference` *after*
  `init_uc` and the industry-model install had succeeded. `deploy.sh` now checks
  the seed catalog is readable before `bundle deploy`.
- **Rendered templates leaked permanently once a deploy died mid-run**: the trap
  restored `app.yaml` and `data_quality.lvdash.json` from backups taken at
  start, so a run that died before its trap left a rendered file that the *next*
  deploy backed up and faithfully restored — pinning the committed dashboard to
  one catalog forever. Files clean against HEAD at start are now restored from
  git, and a surviving rendered template is reported loudly.
- **New app env vars never reached existing deployments**: `_patch_yaml` only
  replaces keys already present, so any var added after a user copied their
  gitignored `app.<env>.local.yaml` was a silent no-op — the deploy printed the
  intended value and the app fell back to a default. `render_app_yaml` now
  inserts missing vars and marks them in the output.
- **Reconciliation verdict stuck on "unknown"**: an idle stream (`event_log` at
  steady state, whose `gold_history_*` table doesn't exist until the first event
  flows) counted as unmeasurable and propagated to the headline, so the panel
  never showed green no matter how well the busy streams agreed. A stream with
  zero rows on both planes is now `idle` and is passed over unless every stream
  is idle.
- **Teardown could not destroy a bundle**: `cleanup.sh` regenerated
  `bundle-targets.yaml` without substituting `${DEPLOYER_EMAIL}`, and Terraform
  reads the unsubstituted `${…}` as a resource reference — so `bundle destroy`
  aborted with "Invalid reference" before deleting anything.
- **Teardown silently skipped the UC purge**: it read a `warehouse_id` key that
  doesn't exist (the setting is `sql_warehouse_id`), and ran the purge under the
  system `python3`, which usually lacks `databricks-sdk`. Both failures were
  warnings, so `--purge-data` reported success while leaving every schema in
  place.
- **Teardown pinned the dashboard template to one catalog**: passing `--target`
  rendered `data_quality.lvdash.json` in place, replacing the `{{UC_CATALOG}}`
  placeholders with no trap to restore them.
- **MVM OEE inflated 100×**: `production.production_line.actual_oee_percentage`
  multiplied `oee_to_now` (already a 0..100 percentage) by 100, producing values
  up to 10,000. Now stored directly as the percentage in both the batch and
  streaming paths.
- **False job failure on success**: the silver mapper and MVM installer no longer
  report `INTERNAL_ERROR` after completing — a raised `SystemExit(0)` is treated
  as a workload failure by serverless `spark_python_task`, so a clean run now
  falls off the end without raising.
- DQX runs correctly inside the serverless `foreachBatch` closure: workspace
  host/token and the MVM catalog are resolved on the driver and passed by value
  (default SDK auth and CLI-promoted env vars are unavailable server-side).
- MVM installer SQL parser strips leading line comments so `CREATE DATABASE` /
  `CREATE TABLE` statements are categorized and ordered correctly.
- DQX check tuning: freshness uses `is_data_fresh` at 5 min (matches the
  `availableNow` drain cadence), and `line_kpi` checks reference the real bronze
  columns (`total_throughput`, `oee_to_now` in 0..100).
- **Sparse incident streams never merged into the governed model**: the bronze
  readers used `startingVersion=latest`, which — under the serverless
  AvailableNow-in-a-loop model that recreates each reader per cycle — is applied
  on every cycle until a checkpoint offset is committed. Empty-at-steady-state
  streams (`event_log`, `quality_checks`) therefore perpetually reset to "only
  rows after now", straddled and skipped any incident written during the
  inter-cycle sleep, never committed a first offset, and never populated
  `asset.failure_record` / `quality.inspection_result` / `quality.ncr`. Removed
  the option so a fresh (uncommitted) reader ingests the full current snapshot;
  high-volume streams with existing offsets are unaffected (the option was
  already ignored on checkpoint resume). Validated live: incident facts now merge
  at seconds latency (offsets `0`→`1`).
- **Streaming job hardened against bronze deletes**: the readers add
  `ignoreDeletes=true` so a delete-only bronze commit (GDPR erasure, retention
  pruning, OPTIMIZE/VACUUM, or cleaning out synthetic rows) is skipped instead of
  failing the continuous stream with "Detected deleted data ... not supported".
- **Governed capacity understated 480×**: `production.work_center`
  `available_capacity_per_shift` stored `machine_registry.nominal_capacity`
  verbatim, but that column is a per-MINUTE rate while the target is per shift.
  Now scaled by the shift length. Same class of unit bug as the OEE ×100 error,
  and it skewed every utilisation metric view built on `work_center`.
- **Replayed micro-batches mutated governed rows**: the streaming MERGE listed
  every column in `UPDATE SET`, so re-processing a batch (which happens on any
  stream restart) restamped `created_timestamp` to "now" and reset `ncr_status`
  to its seed literal — silently re-opening a non-conformance report that
  something downstream had closed. Creation and lifecycle columns are now
  insert-only, making a replay a genuine no-op.
- **Destroy → redeploy left the app with no permissions**: `deploy.sh` only
  captured the app service principal when `app_client_id` was unset, and
  `cleanup.sh` never reset it. After a `bundle destroy` the next deploy therefore
  grafted every grant (UC, Lakebase role, Genie `CAN_RUN`, job permissions) onto
  a principal that no longer existed, and still exited 0. The SP is now resolved
  from the workspace on every run and rewritten when it changes.
- **Governed model unreadable by the app**: nothing granted the app SP access to
  the MVM schemas, so governed Genie and SQL answers came back empty (or denied).
  The installer now grants `USE SCHEMA` + `SELECT` on the four populated schemas
  right after creating them — `init_uc` runs too early to do it.
- **Industry model DDL was unpinned**: the installer tracked upstream `main`, so
  two users installing on different days got different schemas and an upstream
  rename would break the mapper silently. Pinned to a commit; override with
  `--source-ref`.
- **Reconciliation OEE check cried wolf at shift start**: the scale-bug detector
  used a ratio band, but `oee_to_now` is cumulative shift-to-date and starts near
  zero, so a healthy line a minute into its shift (live ≈ 1 vs governed ≈ 74)
  read as 100× corruption and turned the headline verdict red. Detection is now
  by range — OEE is definitionally 0–100, so out-of-range is the real signature.
- Reconciliation robustness: unreadable counts report `unknown` instead of `0`
  (which read as total data loss); rejects are windowed on event time to match
  bronze and gold rather than on processing time; a non-numeric metric can no
  longer 500 the whole panel; results are cached briefly so repeated reloads
  don't fan out; and it uses its own read-only Lakebase connection so a slow scan
  can't stall live SSE reads or disturb another request's transaction.

### Notes

- **Gemini is usable in a tool loop after all.** The "Function call is missing a
  thought_signature" rejection is not a model limitation: the Databricks
  LangChain integration rebuilds tool calls from a fixed `id`/`type`/`function`
  shape and drops the signature Google requires back, even though it arrives
  intact in `additional_kwargs`. The same exchange succeeds through raw
  OpenAI-compatible REST. `agent_server/vendor_compat.py` carries the field
  across; both Gemini endpoints then complete multi-tool ReAct runs with correct
  arithmetic on 4/4 repeats. Gemini can now be assigned to any role via
  `model_roles`.
- **The roster is unchanged despite that**, because Gemini was measured against
  the seat it was a candidate for and lost. Over 8 runs of the shift_briefing
  format contract, average compliance tied (Haiku 4.5 88%, Gemini 3.5 Flash
  89%) but Gemini broke the `##` headings the UI parses on in 3 runs where
  Haiku broke none, at ~80% higher latency. Scored by
  `scripts/dev/compare_shift_briefing_models.py`.
- GPT-5.5 and the GPT-5.6 family reject function tools on chat-completions
  unless reasoning effort is `none`; GPT-5.5-pro is Responses-API only. GPT-5.4
  is therefore the strongest usable OpenAI model in a tool loop.

### Known issues

- **Trace spans still do not upload from Databricks Apps.** The runtime cannot
  reach `*.storage.cloud.databricks.com`, which is where MLflow puts span
  payloads, so a trace arrives as metadata with nothing under it. First recorded
  in 0.2.0; the logging fix above is what made it legible, having spent the
  intervening releases disguised as `'NoneType' object is not iterable`. The
  "Full trace in MLflow" link works regardless and shows the run summary — the
  span detail is what is missing. Storing traces in a Unity Catalog schema was
  tried as a way around it and does not help: with the experiment linked to a UC
  schema via `mlflow.tracing.set_experiment_trace_location`, MLflow 3.14 still
  writes span payloads to the same blob path. Forcing the UC writer with
  `MLFLOW_TRACING_DESTINATION` does change the path, but also changes trace ids
  to `trace:/<catalog>.<schema>/<uuid>`, which the workspace trace URL does not
  address, and its exporter fails separately with `object of type '_io.BytesIO'
  has no len()`. This needs the egress opening, not a client-side workaround.

## [0.2.1] - 2026-07-28

### Fixed
- **Cases tile still showed a nonsensical per-shift count** (e.g. `553,790 / 8,400`)
  after 0.2.0. Two root causes:
  - **Producer**: a warm restart mid-shift rehydrated the *latest* tally for the
    current window, which — for a window whose history was written by the pre-0.2.0
    lifetime-tally build — inherited hundreds of thousands of cases and only
    reset at the next 8h boundary. Added a self-healing sanity clamp
    (`_plausible_seed_cases`): a rehydrated seed above 1.5× a full shift's planned
    output is discarded in favour of an elapsed-time estimate, so the count is
    believable immediately instead of at the next boundary.
  - **UI**: the KPI hero tile rendered `/ {plannedCases}` unconditionally, so even
    with the ratio guard active it showed `count / 8,400`. It now mirrors the
    compact badge — `(plan 8,400/shift)` when the ratio is implausible.

## [0.2.0] - 2026-07-28

Modernizes the platform runtime, removes first-deploy friction for external
adopters, and deepens agent evaluation/observability and ad-hoc data Q&A via
Genie.

### Added
- **Genie "ask the line" data path** (Theme 3b): a first-class path that routes
  free-form data questions to Genie and returns grounded answers with the
  generated SQL and the tables used, surfaced in the UI via a dedicated panel.
- **Expanded MLflow eval harness + tracing** (Theme 3a): larger seed dataset
  (per-specialist / per-incident cases), a deterministic `response_avoids_forbidden`
  scorer plus the built-in `RelevanceToQuery` judge, and trace spans across the
  orchestrator, specialists, and each tool.
- **Judge-alignment workflow**: managed Unity Catalog eval dataset, a Review App
  labeling session for SME labels, and MemAlign-based judge alignment
  (`evaluations/alignment/`), with a runbook.
- **Frictionless adoption** (Theme 2): runnable end-to-end notebook path
  (`notebooks/00..05`), a validated example bundle target, and the architecture
  diagram embedded in the README landing page.
- **Shift progress indicator** in the KPI hero bar (elapsed / remaining), backed
  by UTC-aligned 8-hour shift windows shared by the producer and the client.

### Changed
- **AppKit modernization** (Theme 1): upgraded `@databricks/appkit` and
  `@databricks/appkit-ui` from 0.11.1 to 0.48.0 and adopted the
  `onPluginsReady` server lifecycle.
- Deploy is now more self-healing: idempotent app-SP Unity Catalog grants and an
  idempotent app-SP Genie `CAN_RUN` grant are re-applied on every deploy.

### Fixed
- **Per-shift Cases metric**: the producer now resets `cumulative_cases` at each
  UTC-aligned shift boundary and scopes rehydration to the current window,
  eliminating the nonsensical "% of plan" ratio (e.g. 6233%) caused by dividing
  a lifetime tally by a single-shift plan. The agent snapshot and `KPIStrip`
  also guard the ratio (suppressed above 150%, showing the count instead).
- **Genie Conversation API**: switched to `POST /start-conversation` and
  `GET .../attachments/{attachment_id}/query-result`, fixing 404s on ask-line.
- **`/sim/status` serialization**: `datetime` values are now JSON-encoded via
  `jsonable_encoder`.
- **Resilient SSE reconnect**: the client reconnects with exponential backoff
  (1s–10s) and exposes a "Reconnect now" affordance when stalled, instead of
  getting stuck in a permanent "reconnecting" state.

### Known issues
- Two transitive Python advisories remain and are **upstream-blocked**, not
  fixable via project config today:
  - `protobuf` (GHSA-7gcm-g887-7qv7): the fixed `6.33.5` conflicts with the
    current dependency graph — `ortools 9.14` caps `protobuf<6.32`, and bumping
    `ortools` collides with the `databricks-langchain`/`unitycatalog`/
    `databricks-connect` `numpy<2` stack.
  - `gitpython` (GHSA-94p4-4cq8-9g67 and related): the fixed `3.1.55` is not yet
    available on the deploy build's package index.
  - Both are transitive with low real-world reachability in this app's context.
    Tracked in the backlog to re-pin once upstream ranges move.
- MLflow trace artifact export can fail with `Connection refused` from the Apps
  environment (egress limitation); non-fatal, observability-only.

## [0.1.0] - 2026-06

Initial public release as a Databricks Solution Accelerator
(`databricks-industry-solutions/production-line-copilot`): multi-specialist
GenAI copilot for production-line operations, real-time streaming
simulator (Zerobus → Lakebase), one-command `deploy.sh`, Data Quality
dashboard, and a baseline MLflow eval harness.

[0.3.2]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.3.2
[0.3.1]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.3.1
[0.3.0]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.3.0
[0.2.1]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.2.1
[0.2.0]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.2.0
[0.1.0]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.1.0
