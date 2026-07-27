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
- **Model serving endpoints**: defaults `databricks-gpt-5-mini` and `databricks-gpt-5`; set `model_specialist_endpoint` / `model_advanced_endpoint` in project-settings if your workspace serves them under different names
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
| `model_specialist_endpoint` / `model_advanced_endpoint` | Optional, defaults are the GPT-5 Foundation Model APIs; override only if your workspace serves the LLMs under different endpoint names |

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

For manual grants on `ai_serving` / `ai_chatbot`, run `scripts/grant_app_schemas_lakebase.sql` with the app's service principal UUID.

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

Notebooks in `notebooks/` **00 → 05** mirror the setup tasks. Prefer the bundle setup job for repeatable installs. Notebooks assume widgets from **`00_Parameters`** or a multi-task job passing the same parameters.

Items not covered by notebooks alone (handled by bundle + `deploy.sh`):

- Producer secret + Zerobus endpoint
- Continuous **producer** + **`streaming_quality_sync`** jobs
- App env derivation and deploy
- Lakebase role registration

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
