# Databricks notebook source
# MAGIC %md
# MAGIC # 04 — Deploy App
# MAGIC
# MAGIC This accelerator uses **Databricks Asset Bundles (DABs)** to deploy the app and resources.
# MAGIC
# MAGIC In most customer/workshop environments, deployment is run from a terminal/CI rather than from a notebook.
# MAGIC This notebook is a checklist and a place to record the exact variables used.

# COMMAND ----------

# MAGIC %run ./00_Parameters

# COMMAND ----------

uc_catalog = dbutils.widgets.get("uc_catalog")
uc_schema = dbutils.widgets.get("uc_schema")
warehouse_id = dbutils.widgets.get("sql_warehouse_id")

display(
    {
        "uc_catalog": uc_catalog,
        "uc_schema": uc_schema,
        "sql_warehouse_id": warehouse_id,
        "recommended_command": "./scripts/deploy.sh dev   # or: prod",
    }
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Suggested commands (run in a terminal)
# MAGIC
# MAGIC ```bash
# MAGIC # From the repo root. ``deploy.sh`` runs sync_project_settings.py first,
# MAGIC # then ``databricks bundle deploy`` against the right target, then
# MAGIC # ``databricks apps deploy`` with the env-specific app.yaml swapped in.
# MAGIC ./scripts/deploy.sh dev    # line_manager_dev   → production-line-copilot-dev
# MAGIC ./scripts/deploy.sh prod   # line_manager_prod → production-line-copilot-prod
# MAGIC ```
# MAGIC
# MAGIC ## Important
# MAGIC - Attach **Lakebase** as an App resource so `PGHOST/PGUSER/...` env vars are injected.
# MAGIC - Confirm the SQL warehouse resource is attached (bundle config does this).
