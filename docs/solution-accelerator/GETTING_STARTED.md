# Solution Accelerator: Getting Started & Deployment

This repo is a **Databricks Solution Accelerator** for a **real-time streaming bottling-line demo**: Unity Catalog + Zerobus + **`streaming_quality_sync`** + **Lakebase** + a **Databricks App** (AppKit UI + Python agent server).

**Canonical deploy path:** **[`DEPLOY.md`](./DEPLOY.md)** (numbered steps, profiles, troubleshooting). This document is the deeper component runbook.

**Live pipeline ops:** when the app shows "Live · waiting for new value", the **producer** or **`streaming_quality_sync`** job has usually stalled, confirm both are **RUNNING** in the Jobs UI (see **Troubleshooting** in [`DEPLOY.md`](./DEPLOY.md)).

---

## What you deploy

| Tier                      | Purpose                                | Flow                                                                                                                                                             |
| ------------------------- | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Tier 1: Dashboard**    | Low-latency UI (`/serving/stream` SSE) | **Producer** → Zerobus → Bronze Delta → **`streaming_quality_sync`** → Lakebase **`ai_serving.*`**. App reads **`ai_serving.*`** with **`SSE_SOURCE=lakebase`**. |
| **Tier 2: Agents / SQL** | Rich OT context for specialists        | Reference tables in UC + agent SQL (`context_builder`, `sql_query` tool) against the same `UC_CATALOG` / `UC_SCHEMA`.                                            |

---

## Prerequisites

### Run preflight first

```bash
python3 scripts/preflight.py dev    # or prod
```

Validates local tooling (Databricks CLI, Node 22+, `uv`, `psycopg2`) and workspace resources (auth, warehouse, UC catalog, serving endpoints `databricks-gpt-5-mini` + `databricks-gpt-5`, producer secret scope).

### You must provision (bundle does not create these)

- **Unity Catalog** catalog + target schema
- **SQL warehouse**
- **Lakebase Autoscale** project / branch / endpoint
- **Zerobus Ingest** + server endpoint URL
- **Producer service principal** + Databricks secret for `producer_client_secret_ref`
- **Model serving endpoints**: each agent role prefers a vendor-specific Foundation Model API endpoint (six vendors — see `docs/ARCHITECTURE.md`), falling back to `databricks-gpt-5-mini` / `databricks-gpt-5` for anything your workspace doesn't serve. Set `model_specialist_endpoint` / `model_advanced_endpoint` if those two are named differently here, or `model_multi_vendor: false` to run everything on them
- **Databricks CLI**, **Node.js 22+**, **`uv`**

### Permissions

Ability to: create UC schema/tables; deploy **Databricks Apps**; create **Jobs**; (optional) create **Synced Database Tables** to Lakebase.

---

## Config

**Single source of truth:** `config/project-settings.local.yaml` (copy from `config/project-settings.yaml`).

```bash
cp config/project-settings.yaml config/project-settings.local.yaml
```

Fill required fields, see [`config/README.md`](../../config/README.md). **`app.yaml` / `app.dev.yaml` are auto-derived** at deploy time; do not maintain UC/Lakebase values in two places.

| Setting                                                 | Notes                                                                                                                                   |
| ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `app_client_id`                                         | Leave `SET_ME`, auto-captured after first deploy                                                                                       |
| `src_catalog` / `src_schema`                            | **Empty by default**: setup job generates reference data. Set both only to copy from a seed schema.                                    |
| `lakebase_catalog`                                      | Optional, required only for Synced Tables                                                                                              |
| `model_specialist_endpoint` / `model_advanced_endpoint` | Optional. The **fallback tiers** a role lands on when its preferred per-role model isn't served here; override only if your workspace names the GPT-5 endpoints differently |
| `model_multi_vendor`                                    | Optional, defaults `true`. Set `false` to ignore the per-role roster and run every role on the two tiers above                          |
| `model_roles`                                           | Optional map of `role: endpoint`. Pins one role to a specific model; roles you omit keep the roster default. See the commented example in the file, and `--models` below |

**Check which models your workspace serves** before or after deploying — every deploy prints this, and you can run it on its own:

```bash
python3 scripts/preflight.py dev --models
```

It lists each role, the endpoint that will answer for it, whether that came from the shipped default or your `model_roles` config, and whether this workspace actually serves it. Roles marked `MISSING` still work — they fall back to the tier endpoints — but they lose their preferred vendor, so enable those models under **Serving → Endpoints** or pin alternatives in `model_roles`.

---

## Deploy

```bash
DATABRICKS_PROFILE=<profile> ./scripts/deploy.sh dev    # → production-line-copilot-dev
DATABRICKS_PROFILE=<profile> ./scripts/deploy.sh prod   # → production-line-copilot-prod
```

`deploy.sh` syncs config, guards `SET_ME`, renders app env, bundle deploys (jobs + dashboard + app), captures the app SP, registers Lakebase roles, refreshes `OPS_*` IDs, runs `apps deploy`, and grants the Data Quality dashboard. Profile: `DATABRICKS_PROFILE` (default `DEFAULT`).

---

## One-time data init (automatic)

On a **fresh workspace**, `deploy.sh` auto-detects the empty schema and runs the one-time setup itself, creating the UC schema + tables, seeding reference data, wiring synced tables, and creating the curated **Genie space** (POST `/api/2.0/genie/spaces`, app SP granted `CAN_RUN`, id written back to `project-settings.local.yaml`). No manual step required.

Force it with `RUN_SETUP=1 ./scripts/deploy.sh dev`; skip it with `RUN_SETUP=0`. The setup job is **`line-manager-solution-accelerator-setup`**:

| Task                  | Script                                | Purpose                                                                     |
| --------------------- | ------------------------------------- | --------------------------------------------------------------------------- |
| `init_uc`             | `scripts/init_realtime_uc.py`         | Create UC schema + bronze/gold tables + `inventory_status` view + SP grants |
| `bootstrap_reference` | `scripts/bootstrap_reference_data.py` | Seed reference/dimension tables (generate or copy from `src_*`)             |
| `setup_synced_tables` | `scripts/setup_synced_tables.py`      | Only when `lakebase_catalog` is set                                         |

Confirm continuous jobs are running (shipped **UNPAUSED**):

- **`line-manager-streaming-producer`**
- **`line-manager-streaming-quality-sync`**

---

## Component reference

### 1) Unity Catalog

| Action                 | How                                                                           |
| ---------------------- | ----------------------------------------------------------------------------- |
| Create schema + tables | Setup job task `init_uc` or `scripts/init_realtime_uc.py`                     |
| Seed reference data    | Setup job task `bootstrap_reference` or `scripts/bootstrap_reference_data.py` |

**Upgrades:** If `line_kpi` lacks OEE columns, run `scripts/migrate_line_kpi_add_oee_columns.sql` or recreate from current `init_realtime_uc` definitions.

### After KPI / OEE schema changes

| Step  | Where              | Action                                                                                 |
| ----- | ------------------ | -------------------------------------------------------------------------------------- |
| **1** | **Databricks SQL** | Migrate or recreate `line_kpi_bronze` / `line_kpi`                                     |
| **2** | **Jobs**           | Restart **`line-manager-streaming-quality-sync`** so Gold/Lakebase reflect new columns |
| **3** | **Jobs**           | Confirm **producer** is running (new bronze rows carry OEE fields)                     |
| **4** | **This repo**      | `npm run build:client` then redeploy the app for UI changes                            |

### 2) Lakebase: OAuth roles

`deploy.sh` runs `scripts/init_lakebase_role.py` for the **App SP** and **Producer SP** after bundle deploy. The app runtime resolves its Postgres user from injected **`DATABRICKS_CLIENT_ID`** (not a static `PGUSER` in config).

That script now creates `ai_serving` and `ai_chatbot` before granting on them, and grants both. It used to grant only `ai_serving`, and it ran before the app had ever connected — so on a first deploy the grant failed against a schema that did not exist yet, and chat persistence needed `scripts/grant_app_schemas_lakebase.sql` run by hand. That file remains only for repairing a Lakebase deployed before this change.

### 3) Zerobus producer job

Long-running job: `src/streaming_sim/producer.py`.

| Variable                   | Purpose                                    |
| -------------------------- | ------------------------------------------ |
| `ZEROBUS_SERVER_ENDPOINT`  | Workspace Zerobus URL                      |
| `UC_CATALOG` / `UC_SCHEMA` | Bronze target                              |
| `SIM_SHIFT_ID`             | Must match app (`shift_1` / `shift_1_dev`) |
| Producer SP credentials    | Via `producer_client_id` + secret ref      |

### 4) `streaming_quality_sync`

Continuous Spark job on `pipelines/streaming_quality_sync.py`: reads bronze, applies quality checks, writes **`gold_history`** Delta and **`ai_serving.*`** in Lakebase.

If this job or the producer stops, dashboard data stalls.

### 5) Synced Tables (optional)

UC Gold → Lakebase `public.*` for SQL tools. Set `lakebase_catalog` in project-settings, redeploy, re-run setup job. **Not** the Tier-1 SSE read path.

### 6) Databricks App environment

Key variables (auto-derived unless noted):

| Variable                      | Purpose                                 |
| ----------------------------- | --------------------------------------- |
| `SSE_SOURCE`                  | **`lakebase`**                          |
| `UC_CATALOG` / `UC_SCHEMA`    | From project-settings                   |
| `PGHOST`, `LAKEBASE_ENDPOINT` | From project-settings                   |
| `SIM_SHIFT_ID`                | Must match producer                     |
| `LAKEBASE_SYNCED_DATA_SCHEMA` | Optional; only when Synced Tables exist |
| `DATABRICKS_WAREHOUSE_ID`     | Injected from SQL warehouse resource    |

---

## Validate end-to-end

| Check                    | Success signal                                                                  |
| ------------------------ | ------------------------------------------------------------------------------- |
| Producer                 | Bronze tables receive rows (`machine_state_bronze`, `line_kpi_bronze`)          |
| `streaming_quality_sync` | Rows in Lakebase **`ai_serving.machine_state_history`** for your `SIM_SHIFT_ID` |
| App logs                 | `Lakebase connection established`                                               |
| SSE / UI                 | KPI strip and machines update; OEE non-zero when line is healthy                |
| Agents                   | SQL tools hit `UC_CATALOG` / `UC_SCHEMA`                                        |

Optional: `uv run python scripts/verify_lakebase.py` for a local Postgres smoke test.

---

## Notebook order (optional)

Notebooks in `notebooks/` **00 → 07** mirror the setup tasks (**06** and **07** are the optional governed industry model). Prefer the bundle setup job for repeatable installs. Notebooks assume widgets from **`00_Parameters`** or a multi-task job passing the same parameters.

Items not covered by notebooks alone (handled by bundle + `deploy.sh`):

- Producer secret + Zerobus endpoint
- Continuous **producer** + **`streaming_quality_sync`** jobs
- App env derivation and deploy
- Lakebase role registration

---

## The governed industry model (optional)

Set `mvm_silver_enabled: true` to install the Databricks **Manufacturing**
industry data model alongside the real-time path, and to keep it current from
the same stream (seconds, not a batch window).

Two things are worth knowing before you turn it on:

- **It installs the whole model, not a subset.** That is 13 schemas, ~114
  tables, and ~95 metric views. This accelerator's simulator populates **12
  tables across 4 schemas** (`asset`, `production`, `quality`, `inventory`) —
  everything else is created empty, because the model is installed as the
  standard defines it rather than trimmed to the demo. Expect the install to
  take tens of minutes; it runs once, in the setup job.
- **The incident tables are empty at steady state.** `asset.failure_record`,
  `quality.inspection_result`, and `quality.ncr` only fill when an incident
  flows through the line, so a zero row count there is correct, not a fault.
  The governed Genie space is configured to say so rather than invent rows.

The **Data Integrity** tab in the app is where you verify the governed model
agrees with the live path — the reconciliation panel sits beside the
quality-gate dashboard there.

---

## Publishing checklist

- [ ] `python3 scripts/preflight.py dev` (or prod) passes
- [ ] `config/project-settings.local.yaml` filled (required fields, no `SET_ME`)
- [ ] `./scripts/deploy.sh dev` (or prod) completes
- [ ] **`line-manager-solution-accelerator-setup`** run once
- [ ] Producer + **`line-manager-streaming-quality-sync`** jobs running
- [ ] `SIM_SHIFT_ID` aligned app ↔ producer
- [ ] App: **`SSE_SOURCE=lakebase`**, warehouse + UC vars present

---

## References in this repo

| Topic                 | Location                                 |
| --------------------- | ---------------------------------------- |
| Quality sync pipeline | `pipelines/streaming_quality_sync.py`    |
| Zerobus producer      | `src/streaming_sim/producer.py`          |
| Tier 1 KPI derivation | `src/streaming_sim/line_kpi_derived.py`  |
| Lakebase grants       | `scripts/grant_app_schemas_lakebase.sql` |
| Preflight checker     | `scripts/preflight.py`                   |
| Deploy script         | `scripts/deploy.sh`                      |
