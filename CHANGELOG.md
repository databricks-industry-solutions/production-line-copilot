# Changelog

All notable changes to ProdLine CoPilot are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.7] - 2026-08-06

Everything here was found by watching v0.3.6 run in production rather than by
reading it. Two of the nine were invisible to the test suite by construction:
one lived in the gap between a mock cursor and a real one, and one only showed
up as a stopwatch difference between two panels of the same dashboard. Both
were found by measuring against a live workspace, which is the practice this
release argues for.

**9 bugs fixed · 60 new tests (2,314 → 2,374).**
Since v0.1.0: 160 bugs fixed, 6 security advisories closed, 27 features added,
and the test suite grown from 511 to 2,374.

Counts are measured rather than estimated: `pytest --collect-only` reports
2,153 tests at v0.3.6 and 2,213 here; the TypeScript suites are unchanged at
161 blocks. No dependency changed in this release, so the advisory review
recorded in `SECURITY.md` at v0.3.6 still stands.

### Fixed

- **Recover never closed an event, and only production could tell you.** The
  pool builds every connection with `RealDictCursor`, so rows arrive keyed by
  column name. `mark_machine_events_resolved` read its open-event count with
  `(cur.fetchone() or [0])[0]`, and indexing a dict row by position raises
  `KeyError: 0` — before the guard, before the insert, on every single call.
  The idempotency fix this function received earlier in the release was
  therefore never reached. The suite passed throughout because its fake cursor
  returned a list where Postgres returns a mapping; that fake now hands back a
  real `RealDictRow`, so the substitution the code depends on is the one the
  tests exercise.

- **A triggered incident reached the line tiles in a second and the feed beside
  them a minute later.** The event feed read only `ai_serving.event_log_recent`,
  which the quality-sync job writes once per `Trigger.AvailableNow` pass. The
  producer dual-writes incident starts and resolves to `event_log_history`
  every second, so the Events tab sat a full sync cycle behind the tiles
  reporting the same fault. The feed now reads both and dedupes on the
  `event_id` the producer mints before the fan-out, preferring the closed copy
  when bronze has already landed an end the live row lacks. Reading both
  matters in each direction: the producer never dual-writes micro stops,
  quality holds, changeovers or CIP cycles, so the sync table is still the only
  source for those. `mark_machine_events_resolved` judges openness over the
  same merged view, or Recover would report nothing to clear for an incident
  the operator can read on screen.

- **The dashboard's first paint and the agent's KPIs ran a minute behind the
  line.** Two writers fill the Lakebase serving layer at very different rates:
  the producer dual-writes `ai_serving.*_history` every second, while the
  streaming quality-sync job upserts `ai_serving.*_latest` once per
  `Trigger.AvailableNow` pass — 60 s at the default `TRIGGER_MILLIS`, plus the
  10–15 s of serverless spin-up the job's own comment budgets for. SSE warmup
  and the agent's context bundle both read the `*_latest` tables, so they
  inherited that cycle: the dashboard painted minute-old state before the
  playback cursor took over, and the assistant answered from a KPI row that
  could contradict the tiles rendered beside it. Measured against prod, the gap
  ran 29–62 s depending on where in the cycle you looked — with FILLER still
  reported `stopped` in `machine_state_latest` after the line had recovered.
  The three "latest" readers now collapse the producer-written `*_history`
  tables to the newest row per key, behind the same 300 s wall-clock guard the
  cursor reads use, so a dead producer yields an honest empty result instead of
  a stale row presented as current. Both source tables carry the indexes this
  needs, and the columns are identical, so nothing downstream changes shape.

- **An expired `PGPASSWORD` was trusted forever.** A Lakebase OAuth credential
  lasts an hour. `PGPASSWORD` is the documented way to supply one, and
  `scripts/init_lakebase.py` recommends it, but `_get_pg_password` returned it
  unconditionally — so once exported into a shell it outlived its own validity,
  and every connection afterwards presented the same dead token. Postgres
  refuses that with "OAuth: User is not authorized", which reads as a missing
  grant and sends you to check roles and permissions that were correct all
  along. The deploy's incident reconciliation had been silently skipping its
  Lakebase pass for a day and a half against a role holding SUPERUSER on the
  right endpoint. An expired JWT is now ignored in favour of a fresh credential;
  an ordinary password, which has no expiry to read, is left alone.

- **Clicking "resolved" twice appended two markers.** The guard meant to prevent
  exactly that counted open events with `timestamp_end IS NULL`. The log is
  append-only, so the original row keeps that NULL forever and the second click
  saw the same event still open. It now reconciles against later resolve
  markers, as every other consumer of that table already did.

- **A machine blocked by a jam downstream was reported as starved of input.**
  `machines_starved` counts both flow faults, because the KPI row has no
  `machines_blocked` column to hold the other one. That fold is survivable; the
  label was not. Genie was told the column meant "count of machines starved by
  upstream stoppage", so a line backed up behind a full buffer was described to
  the operator as short of supply — and the two faults are at opposite ends of
  the line, so the advice that follows sends someone the wrong way. Both
  descriptions now say the counter covers starved and blocked, and point at
  `machine_state.status`, which has kept them apart all along.

- **Everyone provisioning Lakebase raced for the same project name.**
  `provision_prereqs.py --with-lakebase`, which `DEPLOY.md` documents, hardcoded
  the project as `prodline-copilot` no matter what the settings said. Two people
  running it in one workspace collided, and anyone who had written their own
  project into `lakebase_endpoint` — the field `deploy.sh` already parses the
  project out of — watched that choice be ignored. It now reads the endpoint
  path, and says which name it is using and why when it falls back.

- **Four defaults contradicted the configuration they shipped alongside.** A
  default is what runs when the yaml is missing, so it decides how the system
  behaves in its least-configured state; these had each drifted to the opposite
  of what every app yaml says. `OPS_DEFAULT_ROLE` shipped as `viewer` and
  defaulted to `operator`, promoting an unrecognised caller — including the
  literal `unknown` used when no identity header arrives — into the role allowed
  to post control actions. The UC schema fell back to `line_manager_ai_kit` in
  four modules and `line_manager_ai_kit_preview` in a fifth, neither of which is
  where the app installs; the first is the seed schema an earlier version copied
  *from* and it still holds stale rows, so the fallback did not fail, it answered
  with months-old numbers. `SSE_BUFFER_SECONDS` shipped as 2 and defaulted to 20,
  opening the stream eighteen seconds further back and replaying forward, which
  looks live and lags. And `OPS_VS_ENDPOINT_ALWAYS_ON` defaulted to true against
  a configuration that names no Vector Search endpoint, inventing roughly $58 a
  month on the cost panel; it now follows `VS_ENDPOINT`, the same rule
  `render_app_yaml.py` uses to derive the pair. A test now walks every
  `os.environ.get` default in `agent_server/` against both shipped yamls, so the
  next one is caught when it is written rather than four at a time.

- **The similar-case index could be built once, and never refreshed after.**
  Provisioning Vector Search for the prod schema turned up three faults in a
  script whose docstring promises it is "idempotent by construction" and "safe
  on every deploy". `gold_history_event_log` keeps a row per snapshot as an
  event progresses, and those snapshots hold *different* root causes and
  corrective actions — the first theory and the eventual finding. The MERGE fed
  all of them to a statement keyed on `event_id`: the first run inserted every
  snapshot, because nothing matches an empty target and Delta does not enforce
  the primary key the table declares, so prod's library held 103 rows for 44
  events and the index kept whichever snapshot it saw last. A library whose only
  purpose is answering "how was this fixed" was as likely to return the interim
  guess as the fix. The second run then failed outright on
  `DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE`. The source is now
  reduced to one row per event, latest closed snapshot winning, and a library
  left duplicated by earlier runs is rebuilt rather than merged into.

  The third fault was quieter and had been live in dev for as long as the index
  existed. A `TRIGGERED` index syncs when it is asked to and at no other time,
  and `ensure_index` saw an existing index, reported it, and returned — so every
  later run refreshed the case library and left the index frozen at what it
  captured on creation. Dev was serving 24 indexed cases against a 37-row
  library, and because the drift is invisible from the outside, the retriever
  kept labelling those answers `retrieval: vector_search`, which reads as the
  healthy path. An existing index is now sent a sync. Dev came back to 37.

  Compounding all of it, `deploy.sh` turns any failure here into a `WARN` and
  carries on, which is defensible for an enrichment step but meant the MERGE had
  been failing without anyone reading the line.

## [0.3.6] - 2026-08-06

Validated the way a user meets it: both environments destroyed down to service
principals and permissions, then rebuilt from an empty workspace and tested end
to end — and then, separately, examined while running, which is where most of
this release came from.

**84 bugs fixed · 856 new tests (1,458 → 2,314) · 2 new features.**
Since v0.1.0: 151 bugs fixed, 6 security advisories closed, 27 features added,
and the test suite grown from 511 to 2,314.

Counts are measured rather than estimated: `pytest --collect-only` reports 1,342
tests at v0.3.5 and 2,153 here, and the TypeScript suites 116 blocks and 161.

### Added

- **A CI gate, so the test suite is something a merge has to pass.** The suite
  was large, behavioural and green, and none of that was load bearing: no
  workflow ran pytest, ruff, tsc, eslint or vitest, so a pull request could
  merge on a green tick with every one of them failing. The only checks that
  existed validated the bundle and published docs, and the bundle one skips
  itself when its repository variable is unset. Two hermetic jobs now run on
  every pull request — pytest with ruff, and tsc with eslint and vitest —
  needing no workspace and no secret, so a fork gets the same gate. The
  Playwright smoke run is deliberately left out: its `webServer` boots the real
  dev server against a warehouse and Lakebase, and a job that is red for
  unrelated reasons is a job people learn to ignore.

  Both jobs call `scripts/check.sh`, which is the same command to run locally,
  so the two definitions of "passing" cannot drift. That indirection earns its
  keep immediately: some GitHub Enterprise organisations disable hosted runners,
  and on such a repository *no* workflow can start — the jobs report failure
  with "hosted runners are disabled" without having run a test, which is how we
  found that this repository has never completed a single Actions run, including
  the two workflows that predate this one. Where runners exist the workflow is
  the gate; where they do not, `./scripts/check.sh` is, and it checks the same
  things.

- **`scripts/check.sh`, one command for every gate.** Runs ruff, pytest, tsc,
  eslint and vitest, reports all failures rather than stopping at the first, and
  skips cleanly when a toolchain is absent. CONTRIBUTING now points at it.

### Fixed

Found by running the deployed app rather than by reading it — most from a single
screenshot of a number that could not be true, which is the cheapest bug detector
this project has. None of these failed loudly, which is why they survived a
release that had already been validated from an empty workspace.

- **Every optimizer divided bottles by 60 and called the answer cases.** The
  line runs in bottles per minute and the plan is written in cases, 24 bottles
  to a case. Five modules — the recovery calculator, schedule recovery, the
  multi-shift planner, the production forecaster and the stochastic scheduler —
  converted with `bpm × minutes / 60`, the minutes-to-hours conversion for a
  *per-hour* rate applied to a per-minute one. At the nominal 420 BPM over a
  480-minute shift that is 3,360 cases against a plan of 8,400: the line looked
  2.5× slower than it is, and nothing raised.

  It did not stop at a wrong figure in a report, because recovery advice is a
  feasibility test. A line that would comfortably make its plan was told it
  could not, and the remedy offered was to run faster than its own stated
  maximum — 4,200 cases in the 240 minutes left needs 438 BPM against a 450
  limit, and was reported infeasible. A flawless shift forecast at 3,280 cases,
  a 61% shortfall invented entirely by a unit conversion.

  The prompts were right the whole time: `prompts/_shared.py` has told the model
  to "divide by 24 for cases/min" since throughput was last corrected, and the
  producer that actually makes the cases divides by `BOTTLES_PER_CASE`. Only the
  arithmetic downstream disagreed. There is now one conversion in
  `optimizers/_units.py`, pinned by test to the producer's constant, with a
  grep-level guard so the mistake cannot reappear uniformly the way it arrived.
  Two existing tests asserted the wrong numbers and were corrected.

  The governed silver model had the same error as a label, advertising the
  line's `throughput_unit_of_measure` as `cases_per_min` when it carries
  bottles.

- **The quality model read its own probability table backwards.** The CPT holds
  P(failure | condition) — FILLER's `fill_level_out_of_spec` is an expert-assigned
  85% chance of a quality failure. It was fed into an odds-form Bayesian update,
  which needs a *likelihood*, P(evidence | failure). Observing that condition
  returned a risk of 0.23 and the label "moderate". Worse, for any value below
  0.5 the update moved risk the wrong way: a recent fail rate of 6% — above the
  5% threshold that admits the evidence at all — produced 0.007, below the 5%
  prior, so noticing a problem left the model calmer than knowing nothing.
  Now a noisy-OR, which keeps the table's meaning: one cause at 0.85 gives 0.86,
  more causes only ever raise risk, and `normal` sets the baseline instead of
  stacking on top of it. A fail rate handed over as `5.0` is also read as 5%
  rather than 500%.

- **The RUL tool could not accept the argument its own error told you to pass.**
  `estimate_rul` never declared `custom_thresholds`, so LangChain dropped it —
  and the maintenance predictor's prompt instructs the model, in step 5, to send
  exactly that. Every machine-specific sensor therefore came back
  `unknown_sensor_type` with a message advising the model to do the thing it had
  just done. The parameter is now in the signature. A half-specified threshold
  no longer defaults `critical` to zero either, which had made any positive
  reading an emergency.

- **A shift with a changeover had its plan read as zero cases.** `shift_4` is two
  rows: a 45-minute changeover with `planned_cases = 0`, then the production
  segment with 7,600. `load_plan_and_constraints` took `LIMIT 1` with no
  `ORDER BY`, so the warehouse was free to return the changeover, and every
  feasibility answer for that shift was computed against a target of nothing —
  a plan that is met before the shift starts. It now orders by time, picks the
  segment with cases to make, and reports the segments and their total. Genie
  was taught the same false "ONE ROW per shift_id" rule and has been corrected.

- **Genie was taught four columns and an enum that do not exist.** The
  `kpi_recommendations` schema was invented outright — `kpi_metric`,
  `condition_type`, `threshold_value`, `severity`, `recommendation`,
  `expected_impact` against a real table of `recommendation_id`, `category`,
  `title`, `description`, `priority`, `estimated_impact` — so every
  recommendation question produced failing SQL or nothing. `shift_crew.role` was
  advertised as `operator | lead | maintenance | quality | supervisor` when the
  roles are `operator | mechanic | qa | supervisor`, and an example query sorted
  by three roles that match no rows. Inventory `status` was described as OK or
  LOW when it is OK, LOW or CRITICAL, with LOW defined wrongly. Per-machine
  `throughput_per_min` and `nominal_capacity` were both labelled cases/min when
  they carry bottles — the same 24× error as the governed model above.

- **The prompt said "no others exist" while three tables did.**
  `maintenance_windows`, and `event_log_bronze` with the `resolution_minutes`
  and `outcome_*` columns the gold view drops, are now listed — the second is
  the only way to answer whether a repair actually held.

- **The project gave three answers to which version it was.** `package.json`
  said 1.0.0, `pyproject.toml` said 0.3.6, and the newest tag said v0.3.5. None
  of it broke anything — the npm version is read by nothing — which is why it
  survived several releases and why anyone checking the repository out to find
  what they had was misled. Pinned to one number, with a test that fails the
  next time they disagree or a version is bumped without release notes.

- **Downtime was charged to OEE twice.** 43.5% against 3,732 cases made at a
  6,283-case pace — about 59% of what the line could have done, so sixteen points
  that no loss on the floor corresponds to. Availability and performance are not
  independent: a stopped line has no availability and no speed, so both factors
  go to zero on the same tick. Taking a plain mean of each and multiplying them
  charges the same downtime to availability, then to performance, then squares
  the penalty in the product. Performance belongs over run time, not planned
  time. It is now weighted by availability, which restores the identity that
  makes the split worth reading: A × P is the time-average of the instantaneous
  A × P. Replaying 19,961 ticks of a real shift through both versions: 59.4% as
  shipped, 65.4% fixed, against 64.8% implied by the cases on the floor. The gap
  scales with downtime, so the tile was worst exactly when someone was most
  likely to be looking at it. The test that asserted the old behaviour was
  asserting the bug.
- **Three unit bugs in what the line reports it is making**, all from one
  screenshot showing 5,680/min next to a plan of about 17.5 cases/min. The
  Lakebase sink read `throughput`, but the snapshot it is handed emits
  `throughput_per_min`, and `dict.get` with a default does not fail — so every
  serving row carried 0.0 while the same tick reached bronze at ~300/min, and all
  nineteen machines looked idle on a healthy line. `total_throughput` summed a
  series line, counting each bottle once per station it passes through; it is now
  the rate at the last station. And conveyors reported 2% speed, because the
  sentinel capacity of 9999 that stops them bottlenecking the line reached
  `speed_pct` as flow/9999 — a belt runs at its set speed or it does not run.
- **A buffer was sized on the sentinel rather than on what flows through it.**
  `build_line` sized each buffer off the faster of its two neighbours, which took
  the conveyor's 9999. Eight of eighteen buffers came out roughly twenty-four
  times too large, so a stop upstream looked contained for minutes after the line
  had really begun to starve — the opposite of what a cascade demo is for. The
  largest buffer drops from 833 units to 35.
- **Five optimizer and tool bugs that returned plausible wrong answers.** The
  Pareto chart summed past 100%, because each category divided its own loss by
  the multiplicative OEE gap; at A = P = Q = 90 the cumulative finished at 110.7%
  and the ROI ranking built on it was distorted with it. Maintenance timing was a
  fixed 10% dressed as a search: expected cost rose monotonically with risk and
  nothing rewarded waiting, so the minimum was always the first candidate,
  whatever the risk curve. The multi-shift MILP was unreachable — cases produced
  is speed × time and both are decisions, so the solver rejected the constraint
  at construction and a guarding `try/except` sent every call to the greedy
  fallback, leaving the Benders master, the speed continuity and the joint
  CIP/maintenance placement as dead code advertising an optimization that never
  ran. IQR quartiles used integer-division nearest-rank, biasing the fences worst
  at n = 10, exactly where the guard admits data. And `depletion_estimate`
  defaulted a missing horizon to zero, so a material two hours from empty
  reported `will_deplete: False` — the caller is an LLM and the argument looks
  optional, so it now returns `None` with a note rather than a confident wrong no.
- **One repaired row stopped the live event feed for six hours.** An operator
  closed a stuck event by setting `timestamp_end`; a Delta streaming source
  rejects a commit that rewrites files, so the stream died and died again every
  cycle after, because the offset never advanced past the rewrite. The reader
  carried `ignoreDeletes`, which covers a delete-only commit and not an update.
  What turned a one-minute failure into a six-hour one is that nothing said so:
  the cycle summary was built from the list of streams the loop had *started*,
  logged before a single query had run, so a stream failing every cycle was named
  among the drained ones every cycle, and the loop awaited `streams.active`,
  which a failed query has already left. "Terminated with error" appears zero
  times in a log containing seven consecutive failures.
- **Incidents the board could not show were still being reported.** The event log
  is append-only — resolving an incident appends a separate `resolved` row and the
  original keeps `timestamp_end` NULL for good — so that column does not mean
  "still happening"; it matches every incident ever started on the shift. Ten
  places in the Genie space said otherwise, in both the table description and the
  example SQL. On prod the two rules disagreed nine to one, which is why the
  assistant reported unresolved machine incidents against an empty board. The
  deploy now also reconciles the data, filling in `timestamp_end` from the resolve
  marker that already closed each incident and ageing out carryover nobody is
  coming back for.
- **The snapshot's shift clock was frozen and its line count did not add up.**
  Time remaining read 240 min in snapshots taken hours apart — exactly what the
  clock says at 12:00 UTC — because the live overlay advanced the timestamp but
  left elapsed and remaining as the cached bundle had computed them. Neither
  environment could have produced a correct number: dev has no scenario row at
  all, and prod's is dated 2025-01-01, putting the shift end 838,000 minutes in
  the past. Separately, "16 running, 2 issues" on a nineteen-machine line was
  missing one: the issue list matched stopped, degraded and starved but not
  blocked, so a machine that cannot discharge downstream counted as neither.
- **Ninety seconds was under the work, and losing meant losing the answer.**
  Reported as a prod-only timeout; it was not. The same question took 88s on dev
  and 93s on prod against a 90s budget, and a budget inside the spread of the work
  it bounds decides coin flips rather than protecting anyone. Measured off the
  event stream: routing at 4.7s, seven tool calls finishing at 38.7s, then forty
  seconds of silence while the model composes, then ten seconds of prose. The
  tools are a third of it. The worse half was what a cut did — tokens go out as
  they arrive but the answer variable is only assigned when a whole message lands,
  so at the timeout it was still empty even though prose had been on the reader's
  screen for ten seconds, and the handler replaced all of it with "analysis timed
  out". Waiting the full budget and watching the answer vanish is worse than
  either finishing or failing fast. The budget is now 150s, clear of the measured
  range, and a cut keeps what was streamed.
- **`analyze_live.py` died with a NameError on its first line.** It reads
  `os.environ` in its argument parser without importing `os`. Turning ruff on
  found it; the other 59 violations were import order and whitespace.
- **The trace diagnostic blamed the wrong thing.** Reported as prod having no
  MLflow traces — prod has 639 spans, newest matching the very question that
  prompted the report. Traces in Unity Catalog are served by a SQL warehouse, not
  by the tracking server, and neither the UI nor the SDK picks one for you, so a
  full experiment reads as empty until you do. The diagnostic only checked
  provisioning, so it printed every check green and then guessed "the app SP may
  lack write access" — sending the reader to re-grant permissions on a working
  system. It now counts the spans, which is the one fact separating "nothing was
  written" from "nothing is reading".

An audit of the deploy path from the point of view of someone cloning this into
an empty workspace, rather than re-running it in the one where it was built.
Every item below worked here and only here.

- **Trace storage no longer links to a schema that does not exist yet.** Trace
  provisioning runs early in the deploy, because the experiment id has to be
  written into `app.yaml` before the app is deployed — well before the one-time
  setup job creates the UC schema. On a fresh workspace the link therefore
  failed with `SCHEMA_DOES_NOT_EXIST`, and because a Unity Catalog trace link is
  permanent and the backend refuses to unset it, the experiment that had just
  been created was unusable. The next deploy fell through to the `-2` fallback
  name, so every attempt left another dead experiment behind and the app wrote
  its spans nowhere. The step now creates the schema itself first; the setup job
  adds its tables to the same one.
- **All 95 metric views were failing silently.** The upstream industry data
  model ships `CREATE SCHEMA` for the domains that hold tables, but not for
  `_metrics`, where every metric view lives. On a fresh catalog all 95 failed
  with `SCHEMA_NOT_FOUND` — on the first pass and again on the retry — and the
  installer still printed its summary and reported success. The schemas are now
  derived from the view statements themselves and created up front, so a model
  that adds a second metric schema is covered without another change here.

- **Anthropic answers opened with 6,000 characters of base64.** The block-list
  filter has always dropped the model's private reasoning, but it only ran when
  the content parsed as JSON. Streaming accumulates chunk content by string
  concatenation, so where the first chunk carried the serialised block list and
  the rest carried prose, the answer arrived as a complete JSON array with the
  text glued *outside* the closing bracket. `json.loads` rejects that as "Extra
  data", the guard returned "not a block list", and the whole payload —
  reasoning signature included — was rendered into the chat as the answer. It
  parses with `raw_decode` now, taking the array off the front and keeping the
  remainder as text. On a live prod answer that is 7,420 characters reduced to
  the 777 the manager was meant to read. Only the non-streaming path was ever
  clean, which is why every earlier check of this passed.
- **The answer now streams as the model writes it.** "Streaming" previously
  described the tool trace only: the specialist's output was accumulated in a
  worker thread and released as one `answer` event, so the user watched tool
  chips animate and then a finished multi-paragraph reply appeared in a single
  jump. The ReAct loop is now consumed in both `updates` and `messages` mode,
  and the text is forwarded token by token. This costs nothing in latency —
  those tokens were already crossing the wire, they were just being held.
  Two filters matter and both were verified against a live endpoint: the
  `tools` node emits on the same stream, so raw tool JSON would otherwise be
  typed into the answer, and tool-call arguments arrive there as fragments. A
  draft is also discarded when the model abandons a preamble to call a tool,
  which is why the streamed text ends up matching the final answer exactly
  rather than accumulating the model's thinking-aloud. The draft stays a
  draft: the `answer` event remains authoritative, since synthesis can rewrite
  it.
- **Multi-turn memory was switched off by omission in the UI.** The server has
  a conversation store, a history window and a context overlay; it created a
  conversation, saved both sides of every exchange and returned the id on each
  answer. The client neither declared `conversation_id` on its event type nor
  sent it back, so every question opened a new conversation and arrived with no
  knowledge of the one before it — "what about the next machine?" answered
  cold, with nothing logging a problem. The client now round-trips the id and
  drops it when the shift changes. Fixing that exposed a second defect
  underneath: `load_history` ordered `created_at` ascending under a `LIMIT 6`,
  returning the *oldest* six messages. That is identical to the newest six for
  the first three turns — long enough to look correct — after which the window
  froze on the opening exchange for the rest of the conversation.
- **The assistant's SSE stream went silent for up to 90 seconds.** Frames are
  emitted per tool call, so a question needing no tools sent nothing between
  the routing status and the final answer. The app sits behind a proxy entitled
  to drop an idle connection, which surfaces as a spinner that never resolves.
  The drain loop already woke once a second; it now emits an SSE comment on
  each idle tick, which the client's reader skips and the token accounting no
  longer bills as model output.
- **A timed-out specialist kept its worker.** `future.result(timeout=…)` stops
  waiting but cannot stop a thread, so an abandoned run held one of six slots
  until it finished on its own — the pool starvation that widening the pool was
  meant to prevent. The ReAct loop now checks a cancellation flag between
  super-steps, bounding the overrun to the call already in flight.
- **The proxy reported the wrong error for any non-JSON upstream response.**
  `proxyToAgent` called `.json()` unconditionally; an HTML 502 or a truncated
  body threw a parse error that became `502: Unexpected token < in JSON`,
  discarding the real status and message. It now inspects the content type and
  forwards the upstream status either way.
- **Action chips claimed to execute and did nothing.** The panel header read
  "Click to execute" and clicking advanced a chip to a green ✅ "done" that was
  purely local, discarded on the next render, and backed by no request: the ids
  the server emits have no endpoint, and write-back is designed but explicitly
  unimplemented. A cosmetic button is bad; one that reports completion is worse,
  because someone stops chasing work the screen says is handled. Removing the
  buttons fixed the lie and left a flat list the panel itself told the operator
  to hand onward, by retyping it. So the buttons are back doing the thing the
  panel actually asks for: one click copies a ready-to-paste handoff carrying
  the system, the action and its provenance. No row reports work as complete,
  and the execution path stays wired for a real connector. The README's Key
  Capabilities said operators could "approve and act on" these; it now says what
  they are and links the write-back design doc, which is explicit that nothing
  is implemented.

- **Vector Search had never once run, in any environment.** Two independent
  defects, each individually silent, and both hidden by the same fallback.
  First, the app could not authenticate: `VectorSearchClient()` with no
  arguments resolves credentials through MLflow, which wants a personal access
  token, and a Databricks App is only ever given OAuth client credentials — so
  the constructor raised on every call. Second, even with credentials the
  parser read column names from `result.columns`, a key no server returns; they
  arrive under `manifest.columns`, so the parse raised `KeyError`. Both
  exceptions were caught and turned into keyword search, which returns
  well-formed, plausible results — so the endpoint stayed online, billed by the
  hour, and served nothing, while the unit test asserted the invented response
  shape and reported the semantic path as green. The client is now handed the
  app service principal explicitly, the parser reads the real envelope, and the
  test fixture is a response captured from a live index.
- **Enabling Vector Search meant hand-editing `app.yaml`, and the deploy step
  read the wrong file anyway.** The provisioning gate parsed a literal
  `app.yaml` — the committed prod template, deliberately blank — instead of the
  gitignored `app.<env>.local.yaml` the deploy actually ships, so it reported
  "not enabled" on every environment including one whose endpoint name had been
  rendered into place seconds earlier. It now reads `$APP_SOURCE_YAML`, and the
  endpoint is set once via `vector_search_endpoint` in project-settings, from
  which `render_app_yaml.py` derives both `VS_ENDPOINT` and the cost panel's
  billing flag — previously two hand-maintained values whose drift is how the
  panel came to invoice ~$58/month for an endpoint nobody had provisioned.
  Clearing the setting turns the feature off, rather than being skipped as an
  unfilled placeholder like every other field.
- **The first Vector Search deploy looks hung.** Creating an endpoint takes
  15–40 minutes and `--wait` prints nothing throughout, on a script that has
  otherwise emitted a line every few seconds. The step now checks whether the
  endpoint already exists and says which kind of run this is before it blocks,
  and the settings file states the cost in both time and money at the point of
  the decision.

- **The setup job created five tables nothing ever writes.** `machine_state`,
  `line_kpi`, `event_log`, `telemetry` and `quality_checks` were left over from
  a layering the pipeline moved off long ago; the producer writes `*_bronze` and
  the quality gate publishes `gold_history_*`. Their only remaining effect was
  harm: they are the names a model guesses first, and a query against an empty
  table returns zero rows rather than an error, so a specialist would answer
  confidently from nothing and the sole symptom was confidence drifting down.
  The DDL is gone, so the same mistake now raises `TABLE_OR_VIEW_NOT_FOUND`,
  which a specialist reports and a human can act on. `setup_synced_tables.py`
  no longer mirrors three of them into Lakebase either — that provisioned a
  billed sync pipeline over permanently empty tables whose output nothing read.
- **A fresh deploy had no quality or event data.** Both tables fill only when a
  scenario fires, so a newly deployed line runs clean and stays empty
  indefinitely, which silently disables `quality_specialist` and
  `retrieve_similar_cases`. The setup job now seeds a shift of routine QC and a
  plausible spread of resolved events via `backfill_quality_and_events.py`,
  which until now no deploy step invoked.
- **Lakebase playback tables were created by a file nobody ran.** The DDL for
  the four `ai_serving.*_history` tables lived only in
  `scripts/migrate_history_tables.sql`. On a fresh Lakebase the producer's
  dual-write failed soft every tick and the live line never animated. The app
  creates them itself now, like every other table it owns.
- **Chat persistence needed a hand-run grant.** `init_lakebase_role.py` granted
  only `ai_serving`, never `ai_chatbot`, and ran before the app had created
  either schema — so the first grant raised, aborted the remaining statements,
  and the deploy printed a warning and carried on. It now creates both schemas
  first, grants both, and fails loudly on a partial result.
- **The placeholder guard passed on unfilled placeholders.** It tested whether a
  value started and ended with `<…>`, so embedded ones such as
  `projects/<your-lakebase-project>/branches/main` were reported as filled and
  failed later inside Lakebase registration. `lakebase_checkpoint_volume` was
  not checked at all and sent streaming checkpoints to a nonexistent catalog.
- **The Data Quality tab embedded a 404 in other people's workspaces.** Three
  things must be true for `/embed/dashboardsv3/<id>` to resolve — the dashboard
  exists, it is published with embedded credentials, and the viewer can read it
  — and all three were assumed. `SET_ME` is a non-empty string, so it passed the
  `-n` test and the publish and grant calls ran against a literal id of
  `SET_ME`; the grants that did land went to the app service principal only, so
  the tab worked for whoever deployed it and 404'd for everyone they showed it
  to; and publishing lived in a bash script that only runs under
  `scripts/deploy.sh` and warned-and-continued on failure, so a plain
  `databricks bundle deploy` produced a dashboard that existed, resolved by
  name, and rendered Databricks' bare "The resource is not available" page with
  nothing anywhere to explain it. `scripts/ensure_dashboard_embed.py` now
  publishes, grants the app SP *and* human viewers (`DASHBOARD_VIEWER_GROUPS`
  for a team), and reads the state back — and it fails the deploy rather than
  warning. The app checks publish status too, so if it is ever wrong again the
  tab shows the reason and the command to fix it instead of a blank Databricks
  page.
- **Vector Search shipped switched on.** `app.yaml` named an endpoint and set
  `OPS_VS_ENDPOINT_ALWAYS_ON=true` by default, so the cost panel billed roughly
  $58/month for an index that provisioning frequently skipped. Both now default
  off, and the skip no longer blames `VS_ENDPOINT` when the real cause is an
  unresolved catalog.
- **Legacy AppKit queries under `config/queries/`.** Never reachable at runtime
  and never validated: five targeted the dead tables, one targeted
  `machine_telemetry` — a table that never existed — one selected a column that
  never existed, and one queried `consumption_log`, also absent. Repointed to
  the live tables, or deleted where they described a schema that is gone.
- **`analyze_live.py` scored broken tools as healthy.** The tools catch their
  own exceptions and return `status: success` with the failure in the body, so
  the payload has to decide. `UNRESOLVED_COLUMN` (how Spark reports every wrong
  column), timeouts, `DIVIDE_BY_ZERO`, errors under a `message` key or past the
  first row, and nested-empty results all read as `ok`. `--since` also defaulted
  to the epoch, letting a trace from an earlier run of the same question supply
  this run's tool statistics; it is now inferred from the run file.
- **The eval gate passed when it measured nothing.** Every threshold passes at
  `n == 0`, which is right for a metric that does not apply to a question and
  wrong for all of them at once — a run against an empty workspace was
  indistinguishable from a green one. A run that scored no assertions now fails.
- **Tests that could not fail.** The intent test asserted its own fixture
  against a copy of the specialist list rather than calling the router; the
  response-quality test built a fake answer by joining the keywords it then
  asserted were present; `test_recursion_limit_set` was `pass`. Two golden
  records were fully satisfiable by restating the question.
- **The SQL validator crashed on startup.** It counted rows across every object
  in `information_schema` as one `UNION ALL`, and a Vector Search index in the
  same schema answers `count(*)` with `DATA_SOURCE_NOT_FOUND` — taking down the
  whole run, which is why it had stopped being run. Counts are best-effort now,
  and coverage extends to `config/queries/*.sql`, which found five of the bugs
  above.
- **The agent could reason about two shifts at once.** `build_context` used the
  `shift_id` it was given while `_overlay_live_state` read `SIM_SHIFT_ID` from
  the environment, so past events could describe one shift and live machine
  state, KPIs and active incidents another. Routing reads `active_incidents`,
  which meant the specialist could be chosen by an incident on a shift nobody
  asked about. The HTTP path never showed it — `_server_shift_id` deliberately
  overrides whatever the browser sends, so the halves agreed by coincidence —
  but `evaluations/run_eval.py` calls `invoke` directly with the shift from its
  dataset, so the judges were the ones seeing it. The overlay now takes the
  shift as an argument and treats the environment as a fallback.
- **A failed specialist claimed a fallback had run.** The synchronous error path
  returned "Falling back to a general analysis" and then did no such thing;
  `_should_skip_synthesis` matches "encountered an error", so the synthesis pass
  was skipped too. It now says no analysis was produced.
- **The timeout was documented as a resource ceiling and is not one.**
  `future.result(timeout=…)` releases the caller, not the thread — nothing in
  Python interrupts a call in flight. The streaming path checks a cancellation
  flag between ReAct super-steps, so it stops after the model or tool call
  already running; the synchronous path is one blocking `invoke` with no such
  seam and can hold a worker past the timeout the user already saw. The
  recursion limit is what actually bounds the work, and the comments now say so.

- **`estimate_rul` could never succeed.** The maintenance_predictor prompt told
  the model to send `{"ts": …}`; the estimator read `r["timestamp_minute"]` as a
  bare subscript, so every correctly-formed call raised `KeyError` — on the one
  tool that specialist has and no other does. Both spellings are accepted now,
  the prompt asks for the right one, and a malformed reading gets an explanation
  rather than a stack trace. The prompt also asked the model to convert
  timestamps to minutes from shift start using a `shift_start` field that does
  not exist in the context; the trend is invariant to the time origin, so it now
  just asks for minutes since the first reading.
- **An unrecognised sensor reported CRITICAL for a healthy machine.** Thresholds
  fell back to `{"warning": 0, "critical": 0}`, and every positive reading is
  above zero, so the tool answered "exceeds critical threshold (0). Immediate
  maintenance required." The prompt makes that the normal case, not an edge: it
  tells the model to pick a real channel from `machine_sensors`, whose names are
  machine-specific and are not the five hardcoded types. The tool now says it
  has no thresholds and asks for the `alarm_low`/`alarm_high` it was already
  reading in step 1.
- **A percentage OEE forecast a perfect shift.** The Monte Carlo simulators
  clamp with `min(1.0, …)` while everything the model can see — the KPI table,
  the context bundle, and `prompts/oee_coach.py` in as many words — carries OEE
  as 0-100. A model that followed its instructions had all 1000 simulations
  pinned to a flawless line, returning P10 = P50 = P90 at the maximum with
  nothing logged. Both simulators and the `calculator` OEE operation now accept
  either scale; `calculator` previously turned `oee(85, 90, 98)` into an
  `oee_pct` of 74,970,000.
- **MTTR calibration averaged an event type nothing writes.** The query filtered
  `event_type = 'major_stop'`, which belongs to the legacy seed generator — the
  producer and backfill write `machine_breakdown`, `minor_stop`,
  `sensor_failure` and friends. Averaging zero rows is not an error, so MTTR sat
  at its 15-minute default in every forecast. It now averages the unplanned
  stops that need a repair, and a test pins the query's event types to the ones
  the backfill actually writes.
- **The shift briefing looked for three fields that do not exist.** It compared
  `current_qty` against `reorder_point` (the column is `on_hand_qty`), called
  inventory items `item_id` (`material_id`), and looked for quality checks with
  `NOK` (the values are lowercase `pass`/`fail`). All three matched nothing
  silently, so the briefing reported no stock risk and no quality holds on a
  shift that had them — on the specialist that runs at every shift change.
- **The shift briefing was handed `sql_query` and no list of tables.** It is the
  one specialist that inlines its own output template rather than importing the
  shared block, and the table list went with it. Its own worked example is "last
  3 events on a specific machine", so the model did the reasonable thing with no
  schema in front of it and wrote `FROM event_log` — zero rows, no error, and a
  handover reporting a clean shift. Naming no table turns out to be as harmful
  as naming the wrong one, and neither dead-table guard could see it: the bad
  name existed only in the model's guess, never in the prompt or the repository.
- **The citation rule shown to all seven specialists named a dead table.** Its
  worked example was "Based on machine_state data…", a table with zero rows,
  fourteen lines before the schema block gives the real name.
- **Two tools returned failures shaped like data.** `retrieve_similar_cases`
  passed the SQL error envelope through as a similar case, so the model got one
  "case" with no root cause and no corrective action and summarised it.
  `load_plan_and_constraints` filtered the error rows out and returned
  `{"plan": {}, "constraints": []}`, which reads as "this shift has no plan"
  rather than "the warehouse is down".
- **Confidence was tallied against the wrong specialist.** `analyze_live` bucketed
  each answer's confidence by the *expected* specialist, so every mis-routed
  question credited a specialist that never ran, systematically rather than
  randomly. Unparsed confidence is now counted too, instead of vanishing and
  leaving a distribution with no denominator.

A second pass, run against the two environments while they were live rather
than against the deploy path. Most of what follows is data that was present,
well-formed and wrong — the kind a pipeline ingests, a quality gate passes and
a dashboard charts without anything failing.

- **Dev's producer was consuming prod's incidents.** Every environment shares
  one Lakebase database and both producers polled the same `scenario_commands`
  table with no shift filter. Whichever polled first claimed the row, applied
  the fault to its own line, emitted the event into its own bronze schema and
  flipped the shared row to `active` — after which the environment the operator
  was watching never saw it pending. Intermittent, because it is a race.
  Rehydration after a restart had never run either: it addressed an attribute
  `CommandStore` has never had, against an already-qualified table name, and
  both raised into a bare `except`. Same symptom, so it hid behind the race.
- **Deploys left the streaming jobs running the previous build.** `bundle
  deploy` replaces the files a job runs from, but a run already in flight keeps
  executing what it started with, and the producer and quality-sync jobs are
  continuous, so they never terminate on their own. Every deploy after the first
  ran the day before's code. Found the hard way: the shift-scoping fix above
  went out to both environments and changed nothing. A fix that deploys cleanly
  and has no effect sends you hunting a second bug that does not exist. The
  deploy now cancels the active runs; continuous brings them straight back.
  The restart step then failed on its own first outing, because `--suffix -dev`
  reads as a flag with no argument to `argparse`.
- **Every read left the connection idle in transaction.** The pooled connection
  runs with autocommit off and `query()` returned straight after fetching, so
  the backend held an ACCESS SHARE lock on everything it had touched until some
  later query happened to end it. The SSE stream reads `scenario_commands` every
  second, so one was almost always open: a deploy's `ALTER TABLE` could never
  take ACCESS EXCLUSIVE, and because an exclusive request queues ahead of later
  readers, every producer poll then blocked behind the ALTER. One dev deploy
  stalled fourteen minutes and froze incident handling in both environments
  while it waited. Reads now end their own transaction, guarded by a write-unit
  depth so a read nested inside `transaction()` cannot discard the writer, and
  the role script sets `lock_timeout` so DDL fails fast instead of taking the
  line down.
- **Recover could not clear three different kinds of event.** It resolves the
  `scenario_command` a producer holds a fault open for, so it was a no-op on any
  event without one and the handler discarded the `count: 0` that said so — the
  button flickered and the row stayed open, indistinguishable from a fault
  refusing to clear. A resolve request for a command the producer no longer held
  in memory, which is every command that outlived a deploy, fell through to the
  terminal UPDATE without emitting the resolve marker the feed pairs events on;
  dev's CASE_PACKER sat active for hours with its command already terminal. And
  the seeded carryover event has no command behind it at all. The endpoint now
  appends the marker itself when no command is holding the fault, so nothing on
  the board is unclearable, and the producer rebuilds an incident from the
  command row when its own memory has been cycled.
- **An open event nothing resolves stayed active forever.** `event_log_bronze`
  is append-only, so the producer cannot close the original row — it appends a
  synthetic `resolved` row and the feed pairs the two up. An event whose marker
  never arrives is therefore active for good, and the backfill seeds exactly one
  as shift carryover. The feed now ages an unresolved event out after one shift:
  carryover is defined against the current shift, so once that shift is over the
  row is either resolved without a marker or stale. An explicit `timestamp_end`
  still wins, and an unparseable start is left visible rather than hidden.
- **The server judged an event open by a field that is never set.** Closure
  lives in the marker row described above, so `timestamp_end IS NULL` does not
  mean "still happening" — it means "written as an opener", which every incident
  row is. The browser has applied the real rule since the feed was built; the
  server had not. Asked to diagnose a stopped FILLER, the snapshot reported three
  open DEPALLETIZER breakdowns closed ninety minutes earlier and no FILLER at
  all, while the line state, read from a different source, correctly named
  FILLER as the only issue. The specialists were handed the same unreconciled
  list under the name `active_events`, and the intent router was shown resolve
  markers as though they were incidents. The rule now lives in one module that
  deliberately mirrors the TypeScript so the two cannot drift.
- **Three million rows of noise on the sensor a machine is judged by.**
  `depal_cycle_time` had no `nominal_value`. The synthesizer jitters 2% around
  nominal and fell back to 0, so the band became ±1 absolute and the sensor
  emitted `uniform(-1, 1)` — a cycle time, in seconds, going negative, on the
  one sensor DEPALLETIZER is primary-mapped to. Nine speed sensors had a
  setpoint but no alarm band, which is what `estimate_rul` projects a trend
  against. And the 2% jitter ignored the band entirely, so a filler with limits
  at 325/335 ml breached its own fill spec at random; jitter is now capped at
  40% of the distance to the nearer limit. A missing nominal raises instead of
  synthesizing around zero.
- **A rate was being accumulated, and a level refilled at someone else's
  threshold.** `insp_vision_nok_count` and `label_vision_nok_count` are declared
  EA/min with an alarm at 10 and were emitted as monotonic counters — over the
  alarm within minutes of every shift, climbing without bound, then dropping to
  zero on restart, which reads as a defect rate that suddenly healed. Both are
  primary-mapped, so that was the number the inspector and labeller were judged
  by. Separately, level refill triggered at a flat 20% whatever the sensor's own
  alarm said: `washer_caustic_level` alarms at 60 and so sat under its own
  threshold for most of a shift, 586 breaches in fifteen minutes on a machine
  reporting `running`. Jitter was applied after the zero floor rather than
  before, so `shrink_film_remaining` reported −0.09% of a roll, and the
  probabilistic refill could leave a `running` LABELLER on an empty label roll.
- **`alert_thresholds` is advertised to every specialist as per-sensor limits
  and holds none.** Its `sensor_name` column carries `speed_pct` and
  `alarm_count` for four machines; not one of the 66 sensors appears. Looking one
  up returns zero rows, silently, which is the trap the same prompt warns about
  two paragraphs earlier. Documented for what it is and pointed at
  `machine_sensors`. Two neighbours in the same prompt: inventory status is
  uppercase `OK`/`LOW`/`CRITICAL` while the prompt states status values are
  lowercase, so filtering it for `low` quietly matched nothing; and
  `machine_bom`, 34 rows mapping material to machine, was missing from a list
  that claims to be exhaustive, so the supply chain advisor could say crown caps
  run out in forty minutes but not that the CAPPER stops.
- **Answers were cut off before the section that says how much to trust them.**
  Every specialist carried `max_tokens=2048`, generous against the prompt's
  250-word contract and not generous at all against a reasoning model, which
  spends most of a completion budget on reasoning tokens nobody sees. The answer
  truncates from the bottom, so what goes is Priority Actions, Estimated
  Recovery and Confidence — and what is left still reads as a finished answer.
  Raising the number only moves the cliff, so the answer paths now set no cap;
  length stays bounded three ways that fail loudly instead of silently. The
  router keeps its cap of 50, because it returns a specialist name.
- **Chat answers rendered literal asterisks around every metric.** Two
  formatting rules collide: bullets lead with a bolded key, and every metric is
  bolded inline. Models apply the second inside the first and emit
  `**OEE **74.2%****`, which markdown cannot nest.
- **The dashboard and the assistant disagreed about OEE.** Line Status showed
  1.3% and the assistant, reading the same shift five minutes later, said 0.0%.
  The line was in a full cascade stop, so the assistant was right. The UI carries
  the previous frame across a tick where OEE is zero while throughput is not,
  because the producer emits `avg_speed_pct=0` for a single tick mid-cascade;
  its comment says "for this tick only" three times over and nothing bounded it.
  A stopped line satisfies the same condition on every tick, so the hold never
  released. Bounded to one tick and moved to a pure function with tests.
- **`*_to_now` was computed from the current tick.** The columns meant "right
  now" while their names promised "since the shift began", and the row
  contradicted itself the moment a line stopped: `oee_to_now` 0.0 next to
  `cumulative_cases` 1965. Every consumer reads them as shift metrics, and it is
  what led the UI to treat a legitimate zero as a compute glitch. They are now
  the time-average since the shift began, so three hours of healthy running
  followed by ten minutes down reads 72.5 rather than 0.0.
- **The QC failures tile queried a table that does not exist.** It asked for
  `quality_checks` with `result = 'FAIL'`; the producer writes
  `quality_checks_bronze` with lowercase values, so the tile rendered "Unable to
  render visualization" in every workspace it was ever deployed to, and would
  have returned nothing even with the right table name. Dashboard SQL was the
  third blind spot in `validate_sql_against_uc.py`, which is why a non-existent
  table shipped at all.
- **Trace links landed on the list the trace was somewhere in.** The workspace
  redirects `/traces/<id>` to a tab whose list is keyed on the full id, and
  `search_traces` returns `trace:/<catalog>.<schema>.<prefix>/<hex>` verbatim, so
  a bare hex matched no row and the page rendered the unfiltered overview —
  looking as though it had ignored the link. A query parameter carries the
  slashes a path segment cannot, once percent-encoded.
- **A deploy could not rebuild a Genie space it had lost.** Provisioning sat
  inside the `RUN_SETUP=1` branch, and `RUN_SETUP` is auto-detected by probing
  for a bronze table. Once the schema existed, every later deploy skipped the
  step, so a space deleted after the first deploy could only be restored by
  deploying into an empty schema. The app meanwhile advertised itself as
  Genie-enabled because the only check was that the environment variable was
  non-empty, so every `genie_query` failed against a space in the trash while
  the System Map drew the connection. Rebuilding it surfaced two more: the
  curated SQLs hardcoded `shift_1` as the default `:shift_id`, so a dev space
  defaulted to a shift its own producer never writes and answered every
  shift-scoped question with "0 machines" — a query that runs cleanly and
  returns nothing. And Genie spaces are workspace files, so two environments in
  one workspace collide on name.
- **Nothing said when trace storage had not been provisioned.** Reported from
  another workspace: the app deployed, the experiment existed, and every span
  was dropped, because the app was still writing to the bundle's classic
  experiment. Export is async and non-fatal and the deploy treats provisioning
  as non-fatal on purpose, so the last thing on screen was "Deploy complete"
  while the step's own failure sat five hundred lines up. The deploy now reports
  it under the summary, including the fact that a UC link is permanent, so a
  half-linked experiment is scrap rather than something to retry into.
  `scripts/diagnose_trace_storage.py` separates the three causes, which are
  indistinguishable in the UI.
- **Sixteen assertions had stopped running while the suite reported green.**
  `EventFeed.test.ts` imported its subjects through the component, which
  re-exports them but also pulls in charts that resolve a nested dependency that
  is not installed, so the file failed to load. It now imports from the utility
  module directly.
- **The System Map and About tab still sold a deleted feature.** "Ask the line"
  was removed, and both surfaces went on drawing it as a live product surface,
  the map with a `DIRECT · NO ORCHESTRATOR` tag. The README screenshot showed it
  too.

### Changed

- **`retrieve_similar_cases` told the model it was doing semantic search when it
  usually is not.** The docstring is what the LLM reads when deciding whether to
  call a tool and how much to trust the result; it named Vector Search
  unconditionally, while the keyword path is the default (an endpoint bills
  hourly). It now describes both paths and points at the `retrieval` and
  `match_score` fields that say which one answered. The module header, which
  claimed no index exists anywhere, was left behind by this week's Vector Search
  work and has been rewritten.
- **The governed-model note assumed the governed model is deployed.** It is
  optional, and where it is absent the specialist got told to expect zero rows
  from tables that do not resolve. The note now names that case and sends the
  model back to the live tables instead of reporting a data problem with the
  line. Unlike the Genie suffix it cannot self-suppress: these prompts are
  registered in the Prompt Registry as constants, so a prompt that varied by
  environment would no longer match its registered version.
- **Docs corrected against the code.** `ARCHITECTURE.md` described
  `langgraph.prebuilt.create_react_agent` with a recursion limit of 10; all
  eight specialists migrated to `langchain.agents.create_agent` and the limit is
  16. `README.md` claimed each specialist reaches its own governed Genie space —
  `production` serves three, `asset` serves two, and the shift briefing has
  none, as `ARCHITECTURE.md`'s own table already said. The README also now
  states that the spaces are opt-in per domain via `GENIE_SPACE_*`, which a
  reader previously had to reach `/ops/topology` to discover. `tests/docs/`
  pins both claims to the code so they cannot drift again silently.

## [0.3.5] - 2026-08-04

### Added
- **Prompts are versioned in Unity Catalog.** All eleven prompts the app sends
  — eight specialist system prompts, the router's system and classification
  prompts, and the synthesis template — are published to the MLflow Prompt
  Registry as `<catalog>.<schema>.prompt_<slug>`, with an author, a commit
  message and a `@production` alias. A wording change is now a version you can
  diff and point a trace at, rather than a line in a Python file. `deploy.sh`
  publishes on every deploy and `scripts/register_prompts.py` runs standalone.
  Registration is idempotent: each prompt is compared against its latest
  registered version and only rewritten when the text differs, because a
  per-deploy run that always writes would bury the versions that mattered under
  eleven identical ones. The app does **not** read prompts back — the constants
  in `agent_server/prompts/` are still the runtime source of truth, so a
  registration failure is non-fatal and leaves behaviour unchanged, and editing
  a version in the UI changes nothing that runs. Specialists that own a
  governed domain have `governed_domain_prompt(<domain>)` appended at
  construction, which is deployment-specific and cannot be pinned; those
  versions are tagged with the domain and the suffix so the difference reads as
  intended rather than as a stale registry.

### Fixed
- **Traces recorded the question and not the answer.** Every specialist span
  set its inputs and never its outputs, so the MLflow UI rendered an empty
  "Model output" for each one and the traces could not be used for the thing
  they exist for — judging answer quality. Spans now carry the answer text and
  the self-reported confidence, and `synthesize_response` is traced too, so
  what a trace stores is what the manager actually read rather than the
  specialist's draft. Confidence is also promoted to a trace tag, which makes
  hedged answers findable in bulk
  (`mlflow traces search --filter-string "tag.confidence = 'LOW'"`) instead of
  needing every trace opened by hand. A test walks the module's AST and fails
  any manual `start_span` block that sets inputs without outputs, because the
  original omission was invisible from outside the UI.
- **The trace link in chat 404'd against a Unity Catalog-backed experiment.** A
  UC trace is identified by its storage location — `trace:/<catalog>.<schema>.<prefix>/<hex>`
  — and interpolating that whole string into the UI's trace route splits one
  path segment into three, so the router matched nothing. The page rendered its
  own 404 while the server still answered 200, which is why nothing server-side
  reported it. Only the trailing hex now reaches the URL, and an id with
  nothing usable in it falls back to the experiment's Traces tab rather than
  building a link that ends in a bare slash. Classic `tr-<hex>` ids are
  untouched.
- **The unit suite no longer tries to export traces.** Tracing is disabled for
  the session in `tests/conftest.py`, so tests stop emitting `Failed to send
  trace` warnings and stop depending on network reachability.

### Removed
- **The "Ask the line" panel is gone, along with `/api/ask-line` and
  `agent.ask_line`.** It sent free-form questions straight to a Genie space
  configured over the "current" tables — `line_kpi`, `machine_state`,
  `quality_checks` and friends — which are DDL that nothing writes: the
  streaming pipeline fills the `*_bronze` and `gold_history_*` tables instead,
  and the dashboard reads Lakebase. So the panel returned empty result sets to
  questions the tab beside it was answering with live numbers, which is worse
  than not offering the feature. The specialists reach Genie through the
  governed per-domain tools and are unaffected; SQL provenance still appears
  under chat answers. Bringing it back means pointing it at a Genie space over
  tables that hold data.

- **Spans stopped being dropped, because trace storage moved to Unity
  Catalog.** A classic experiment uploads span payloads to
  `<region>.storage.cloud.databricks.com`, and the Databricks Apps runtime gets
  `Connection refused` from that host: trace metadata landed and every span was
  discarded. Because export is async and non-fatal, nothing surfaced it — the
  MLflow UI simply showed traces with no spans, which is how this survived
  since 0.2.0. `scripts/deploy.sh` now provisions a purpose-made experiment
  linked to a UC schema, where spans are written to Delta through the workspace
  host the app already reaches for SQL and model serving.

  It needs its own experiment rather than the bundle's because a UC trace
  destination can only be linked to an experiment that holds **no traces**, and
  the bundle's has been collecting them since the app first ran. That also
  fixes the ordering: provisioning runs after the app service principal is
  known, so it can be granted, and before the app deploy, because one written
  trace makes an experiment unconvertible forever. Both orderings are pinned by
  tests, since getting either wrong yields a successful deploy with silently
  broken tracing.

  Two things are worth knowing before changing any of this. A link is
  permanent — `unset_experiment_trace_location` exists in MLflow but Databricks
  rejects it, so an experiment linked wrongly is scrap and the only repair is a
  new one. And `set_experiment_trace_location` is the wrong way to make the
  link: it returns success and writes the storage-table tags while registering
  no telemetry destination, which is what export actually routes through, so
  the experiment reads as correctly configured and discards every span.
  `mlflow.set_experiment(trace_location=UnityCatalog(...))` is used instead, and
  the destination tag is verified afterwards rather than trusted — a link
  without one fails the step and leaves the app config alone, and an experiment
  already in that state is skipped rather than reused.

## [0.3.4] - 2026-08-04

### Added
- **The Cost panel can now tell the app's warehouse usage apart from everyone
  else's.** App-attributed DBU credited the app with every DBU the warehouse
  burned, which is right for a dedicated warehouse and wrong for a shared one —
  on a warehouse the app shares with analysts and jobs, it was reporting their
  spend as the app's. A new card apportions the warehouse's DBU by each
  tenant's share of query time, read from `system.query.history` and matched on
  the app service principal plus an `app:prodline-copilot` query tag the SQL
  tool now stamps on its sessions. It sits alongside the existing figure rather
  than replacing it, so the two can be compared. Warehouses bill for uptime
  rather than for queries, so this apportions idle time across tenants in
  proportion to their usage — a convention, stated as one in the panel, not a
  per-tenant measurement. An idle warehouse attributes its DBU to nobody
  instead of dividing by zero and handing the whole bill to the app. Query tags
  are Public Preview, so a workspace that rejects the session parameter falls
  back to an untagged connection and identity-only matching rather than leaving
  the app with no warehouse connection at all. The split is cached for ten
  minutes: `system.query.history` is account-wide for the region with nothing to
  prune a warehouse filter against, so the aggregate costs ~26s in the deployed
  app, and running it per refresh would have made the Cost tab the app's
  largest warehouse consumer — and a self-fulfilling one, since the panel's own
  queries run as the app service principal and would inflate the share they
  report. Only the split is reused; it is repriced against each load's fresh DBU
  so the panel never shows two different warehouse costs at once.

### Changed
- **Billing queries now prune on `usage_date`.** They filtered on
  `usage_start_time`, which is not the partition column, so each panel load
  scanned the account's entire billing history six to eight times over. The
  added date predicate is a deliberate superset of the time window, so it
  changes cost and latency but not results.
- **Dollar figures render at two decimals.** `$341.6100` reads like a settled
  invoice; it is DBU multiplied by a rounded list-price guess and is
  comfortably ±20%. Sub-cent live line items keep four decimals so they do not
  collapse to `$0.00`.
- **The two dollar figures say which scope they cover.** The headline tile and
  "Total app est. USD" legitimately differ by however much other work runs
  alongside the app, but both were labelled as though they were the same
  quantity, so the pair read as a contradiction.

### Fixed
- **Opening the Cost tab froze the whole app, live stream included.** Present
  since before 0.3.3, so this affects anyone already running the app.
  `/ops/cost-summary` was declared `async def` but its body is half a dozen
  sequential warehouse round trips and no `await` at all, so FastAPI ran that
  blocking work on the event loop rather than in its threadpool. For as long as
  it took, the process served nothing else: requests queued and `/serving/stream`
  stopped emitting. Measured against a deployed app, a cold load stalled the SSE
  stream for 44.9s against its normal 1.04s tick — the live data simply stopped
  for three quarters of a minute. The rollup now runs in a worker thread, which
  is the pattern every other handler in the module already used; the same load
  now leaves a worst-case gap of 1.07s. A test walks the module's AST and fails
  any `async` route that makes a warehouse or SDK call inline, with
  `ops_control_action` recorded as a known pre-existing offender rather than
  quietly grandfathered.
- **The Cost & Obs panel reported the whole account as though it were this
  app.** Every billing query was scoped by `DATABRICKS_WORKSPACE_ID`, a
  variable read in one place and set in none — not `app.yaml`, not the bundle,
  not `render_app_yaml.py`. Unset, the filter vanished and the totals covered
  every workspace the account bills for, so in a shared demo workspace the
  panel was quoting other teams' jobs. The workspace id now comes from the SDK,
  which always knows it because the app authenticates against that workspace;
  the env var still overrides for local dev. If neither resolves, the panel
  says so in a banner rather than silently widening.
- **Two numbers on the same screen priced the same row differently.** The
  components table classified SKUs with a hand-written SQL `CASE` and the
  verification figure below it with a hand-written Python function, and the two
  had drifted: the `CASE` checked `sku_name` for `MODEL` but not
  `billing_origin_product`, so a `FOUNDATION_MODEL_TRAINING` row was "Other" at
  $0.55/DBU in one and "Model Serving" at $0.07/DBU in the other — a 7.9×
  disagreement. Both are now generated from one ordered rule table, with a test
  over realistic SKU names holding them together.
- **Live mode's Total was larger than the rows it sat under.** The
  synced-tables charge was in the total but had no line of its own, so the
  breakdown came up $0.30/h per synced table short with nothing on screen to
  explain it. The total is now summed from the rendered rows, which makes an
  unlisted cost unrepresentable, and synced tables have a row.
- **An idle dedicated Genie warehouse was billed a full hourly rate.** Unlike
  the main warehouse line it was never gated on running state, so a configured
  but auto-stopped warehouse invented $0.70/h that Databricks never charged.
- **Billing freshness was off by the machine's UTC offset.** A Spark
  `TIMESTAMP` renders without an offset and `.astimezone()` reads a naive
  datetime as local time, so on a UTC+2 host every row looked two hours older
  than it was and the "stale by >6h" banner fired two hours early.

## [0.3.3] - 2026-08-04

Closes the two npm advisories 0.3.1 deferred. Both are build-time dependencies
and no runtime code changed, so no redeploy is required. The npm tree is now
clean: 938 packages, zero known advisories.

### Security
- **`postcss` 8.5.19 → 8.5.25** (GHSA-fxqj-rqcc-2cmp, CVE-2026-69153,
  MODERATE). An incomplete fix of an earlier advisory: an attacker-controlled
  `sourceMappingURL` can read arbitrary `.map` files when `from` is unset. Our
  use is Tailwind and Vite compiling first-party CSS at build time, so there is
  no attacker-controlled stylesheet to begin with.
- **`brace-expansion` 5.0.8 → 5.0.9** (GHSA-rgw5-rvv9-x895, CVE-2026-69152,
  HIGH). Denial of service through unbounded intermediate arrays, bypassing the
  mitigation added for CVE-2026-14257. Reaches us transitively through
  `minimatch`; the existing `overrides` pin moved with it.

### Notes
- **The registry block was local, not the VPN.** 0.3.1 deferred these because
  `registry.npmjs.org` was unreachable and the corporate proxy was the obvious
  suspect. It was not: `/etc/hosts` pins `registry.npmjs.org`, `pypi.org` and a
  long list of package mirrors to `127.0.0.1`, so the connection is refused
  locally before it leaves the machine. That is managed tooling steering builds
  at the internal mirror, and it was left alone.
- **The upgrades came from Dependabot's resolution rather than the registry.**
  With no way to run `npm install`, this applies the patches from the two
  Dependabot PRs, which are computed against the real registry. Both were based
  on the same tree as ours, so they applied unmodified. The new constraints are
  already satisfied by what is locked — `postcss` 8.5.25 wants `nanoid ^3.3.16`
  and 3.3.16 is present, `brace-expansion` 5.0.9 wants `balanced-match ^4.0.2`
  and 4.0.4 is present — so nothing cascades. The consumers still resolve:
  `@tailwindcss/postcss` wants `^8.4.41`, `vite` wants `^8.5.6`, `minimatch`
  wants `^5.0.8`.
- **The integrity hashes are trusted, not verified locally.** They come from
  GitHub's resolution against npm, and the tarballs cannot be fetched here to
  check them. `node_modules` still holds the old versions, so the test and
  build gates above ran against 8.5.19 and 5.0.8; the first clean `npm ci`
  is what actually exercises these. That is the same trust placed in merging a
  Dependabot PR, but it is worth stating plainly.
- **Python is unchanged.** `cryptography` and `protobuf` still carry the five
  advisories described in 0.3.1, for the same reasons: an upstream `mlflow`
  cap and a Python 3.11 floor.

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

[0.3.6]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.3.6
[0.3.5]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.3.5
[0.3.4]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.3.4
[0.3.3]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.3.3
[0.3.2]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.3.2
[0.3.1]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.3.1
[0.3.0]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.3.0
[0.2.1]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.2.1
[0.2.0]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.2.0
[0.1.0]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.1.0
