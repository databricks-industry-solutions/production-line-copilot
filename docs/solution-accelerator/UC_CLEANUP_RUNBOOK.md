# Unity Catalog cleanup runbook (manual)

Use this **only** after you have retargeted jobs, apps, and Synced Tables to the **current** catalog/schema in `config/project-settings.yaml`. Nothing in this repo executes drops against your workspace.

## 1. Inventory (no changes yet)

- [ ] List bundle deploy targets and note `catalog` / `schema` per target (`config/project-settings.yaml` → `config/bundle-targets.yaml`).
- [ ] In each workspace: **Jobs** → search `line-manager`; **Apps** → note attached resources and env `UC_CATALOG` / `UC_SCHEMA`.
- [ ] **Synced Tables**: Lakebase UI → note UC sources you still need.
- [ ] **Genie / dashboards / SQL**: search for old FQNs (`classic_stable_*`, unused schemas under `main`).

## 2. Stop consumers

- [ ] Stop or delete obsolete **continuous** jobs (e.g. producer, `streaming_quality_sync`) that pointed at retired catalogs/schemas.
- [ ] Pause or update **Databricks Apps** that still reference old UC paths.

## 3. Drop order (Databricks SQL / UI — you execute)

Work **inner to outer**: tables/views → schema → catalog (only if empty and policy allows).

1. Optional: export critical tables (`CREATE TABLE … AS` or backup volume) for anything you might need later.
2. `DROP TABLE` / `DROP VIEW` on objects that are **confirmed** unused (repeat per schema).
3. `DROP SCHEMA … CASCADE` only when no remaining dependencies and governance approves.
4. `DROP CATALOG` only when the catalog is dedicated demo data and your admin model allows it.

Replace placeholders in your own SQL editor; **do not** commit workspace-specific `DROP` scripts with real names unless your org requires it.

## 4. Verify

- [ ] `databricks bundle validate` for each target you still use.
- [ ] Smoke: producer + `streaming_quality_sync` + app + Synced Tables path for one `shift_id`.

## 5. Repo hygiene (optional)

- [ ] Remove stale host/catalog names from **local** notes or old `databricks` state under `/.databricks` if they confuse CLI profiles.
