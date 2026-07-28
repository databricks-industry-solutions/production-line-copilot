# Changelog

All notable changes to ProdLine CoPilot are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
GenAI copilot for brewery packaging-line operations, real-time streaming
simulator (Zerobus → Lakebase), one-command `deploy.sh`, Data Quality
dashboard, and a baseline MLflow eval harness.

[0.2.1]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.2.1
[0.2.0]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.2.0
[0.1.0]: https://github.com/databricks-industry-solutions/production-line-copilot/releases/tag/v0.1.0
