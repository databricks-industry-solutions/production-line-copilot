# ProdLine CoPilot: v0.2.0 Release Plan

Status: Deployed + smoke-tested on dev (all themes complete and verified live)
Owner: mohammad.khelghati@databricks.com
Baseline: v0.1.0 (public: databricks-industry-solutions/production-line-copilot)
Last updated: 2026-07-28

Progress:
- [x] Theme 1: AppKit 0.48 modernization + deploy self-heal grants
- [x] Theme 2a: runnable + accurate guided notebook path (00..05)
- [x] Theme 2b: committed example bundle target; `bundle validate` passes on fresh clone
- [x] Theme 2c: architecture diagram embedded in README
- [x] Theme 3a: eval dataset grown (12 -> 33), forbidden-phrase scorer, orchestrator/specialist spans, docs/EVALUATION.md
- [x] Theme 3b: first-class `ask_line` Genie path + `/api/ask-line` + AskLinePanel UI with SQL provenance + eval case
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
`scenarios.py`, and `data/seed_v1.jsonl`. `agent_server/agent.py` captures
MLflow trace spans via `_capture_mlflow_trace_spans()`.

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
- **MLflow trace export from Apps compute (non-fatal).** The dev app logs repeated
  `Connection refused` to `us-east-1.storage.cloud.databricks.com` when uploading
  MLflow trace artifacts — traces don't persist and the retries add log noise.
  App endpoints are unaffected (export is async). Investigate Apps egress / trace
  destination config, or disable artifact upload for the app runtime. Impact: Low
  (observability). Effort: S.
- **Split `start_server.py` (2770 lines) into FastAPI routers.** Pure refactor;
  defer until live deploy/smoke-test is available to validate route behavior.
  Impact: Med (maintainability). Effort: M.
- Real-data connector example (SCADA/MES via Lakeflow Connect) to complement
  synthetic mode. Impact: High. Effort: L.
- Configurable line topology beyond the fixed 19-machine line. Impact: Med.
  Effort: M.
