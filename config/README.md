# Project settings (catalog, schema, workspaces)

## Files

| File                                  | Purpose                                                                                                                                                                                                                |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`project-settings.yaml`**           | **Public template, placeholders only.** Ships in git with `SET_ME` / `<your-*>` values. Do not put real workspace IDs here.                                                                                           |
| **`project-settings.local.yaml`**     | **Edit this, gitignored.** Your real workspace host, warehouse, UC catalog/schema, Lakebase endpoints, producer SP, Zerobus URL, etc. Bootstrap: `cp config/project-settings.yaml config/project-settings.local.yaml` |
| **`bundle-targets.yaml`**             | **Generated, gitignored.** Databricks Asset Bundle `targets`. Regenerated automatically by `./scripts/deploy.sh` (or run `python scripts/sync_project_settings.py` manually).                                         |
| **`bundle-targets.example.yaml`**     | **Committed, non-sensitive.** A placeholder `example` target so `databricks bundle validate --target example --profile <your-profile>` works on a fresh clone (before you have generated the real targets). Not for deploy.                                        |
| **`../app.yaml` / `../app.dev.yaml`** | **Auto-derived at deploy time** from `project-settings` via `scripts/render_app_yaml.py`. Operators do not edit these by hand; deploy creates/updates gitignored `app.local.yaml` / `app.dev.local.yaml` siblings.     |
| **`../databricks.yml`**               | Bundle name, shared `variables`, `resources`, and `include: ./config/bundle-targets.yaml`.                                                                                                                             |

## Single source of truth

Fill **only** `config/project-settings.local.yaml`. At deploy time:

- **`scripts/render_app_yaml.py`** copies `UC_CATALOG`, `UC_SCHEMA`, `PGHOST`, `LAKEBASE_ENDPOINT`, `SIM_SHIFT_ID`, and `GENIE_SPACE_ID` into the app env yaml.
- **`scripts/refresh_app_yaml_ids.py`** writes fresh `OPS_*` resource IDs (jobs, Data Quality dashboard) after the bundle creates them.
- The app service principal UUID (`app_client_id`) is **auto-captured** from `databricks apps get` on first deploy, do not paste it manually.

### Required fields (deploy fails fast if still `SET_ME`)

| Field                        | Purpose                                                 |
| ---------------------------- | ------------------------------------------------------- |
| `workspace_host`             | Target Databricks workspace URL                         |
| `sql_warehouse_id`           | SQL warehouse for jobs and agent SQL                    |
| `uc_catalog` / `uc_schema`   | Live streaming UC target (setup job populates tables)   |
| `producer_client_id`         | Zerobus ingest service principal                        |
| `producer_client_secret_ref` | Databricks secret ref, e.g. `{{secrets/<scope>/<key>}}` |
| `lakebase_pg_host`           | Lakebase Postgres host (`PGHOST`)                       |
| `lakebase_endpoint`          | Lakebase resource path for OAuth credentials            |
| `zerobus_server_endpoint`    | Workspace Zerobus Ingest URL                            |

### Optional / special cases

| Field                        | Notes                                                                                                                                     |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `src_catalog` / `src_schema` | **Empty by default**: setup job **generates** reference/dimension data in-workspace. Set both only to copy from an existing seed schema. |
| `lakebase_catalog`           | Required only for Synced Tables (`setup_synced_tables` task). Empty → task is skipped.                                                    |
| `app_client_id`              | Auto-filled after first deploy; leave as `SET_ME` on a fresh workspace.                                                                   |
| `genie_space_id`             | Leave as `SET_ME` on a fresh workspace, `deploy.sh` auto-creates the curated Genie space on first deploy and writes the id back here.    |

`${DEPLOYER_EMAIL}` in `lakebase_syncer_pg_user` (and similar) is substituted by `deploy.sh` from `databricks current-user me` (override with `DEPLOYER_EMAIL=…`).

## Profiles

Step-by-step deploy: **`docs/solution-accelerator/DEPLOY.md`**.

| Profile                 | Bundle target       | Deploy command             |
| ----------------------- | ------------------- | -------------------------- |
| **`line_manager_dev`**  | `line_manager_dev`  | `./scripts/deploy.sh dev`  |
| **`line_manager_prod`** | `line_manager_prod` | `./scripts/deploy.sh prod` |

All deployed profiles use **`zerobus_ingest_enabled: true`**: the bundle provisions the setup job, Zerobus producer, and `streaming_quality_sync` job.

## Commands

```bash
# Validate prerequisites before first deploy
python3 scripts/preflight.py dev    # or prod

# Deploy (syncs config, renders app env, bundle + app deploy)
./scripts/deploy.sh dev
./scripts/deploy.sh prod

# Optional: run preflight as part of deploy
RUN_PREFLIGHT=1 ./scripts/deploy.sh dev

# CI: fail if bundle-targets.yaml is out of date with project-settings
python scripts/sync_project_settings.py --check
```

`DATABRICKS_PROFILE` selects the CLI auth profile (default `DEFAULT`). The profile's `host` must match `workspace_host` for the target you deploy.

When `lakebase_catalog` is empty, sync emits `__SKIP__` for that job parameter so `setup_synced_tables` exits cleanly until you set a real catalog.

## AppKit SQL (`config/queries/*.sql`)

Queries use the template prefix `main.line_manager_ai_kit.`. At runtime the Express server rewrites that to **`${UC_CATALOG}.${UC_SCHEMA}.`** (`server/sqlRewrite.ts`). Override locally with `SQL_TEMPLATE_CATALOG` / `SQL_TEMPLATE_SCHEMA` if needed.

These are AppKit scaffolding: the live UI reads Lakebase over SSE, and nothing in the app currently calls `executeAnalytics2Query`. Because nothing executed them, nothing caught that several had drifted onto tables and columns that no longer exist. `scripts/validate_sql_against_uc.py` now EXPLAINs each one against the real catalog, so they stay true whether or not anything runs them — treat a failure there as a real bug, not as noise from unused code.
