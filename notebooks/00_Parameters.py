# Databricks notebook source
# MAGIC %md
# MAGIC # 00 — Parameters (Solution Accelerator)
# MAGIC
# MAGIC This notebook defines the parameters used throughout the installation flow.
# MAGIC
# MAGIC **Goal:** make the setup repeatable across workspaces without editing code.

# COMMAND ----------

dbutils.widgets.text("uc_catalog", "main", "Unity Catalog catalog")
dbutils.widgets.text("uc_schema", "line_manager_streaming", "Unity Catalog schema (streaming)")
dbutils.widgets.text("sql_warehouse_id", "", "SQL Warehouse ID (serverless recommended)")

dbutils.widgets.text("lakebase_catalog", "", "Lakebase catalog name (for Synced Tables)")
dbutils.widgets.text("lakebase_schema", "public", "Lakebase schema name")

# Optional source schema to COPY seed reference tables FROM. Leave BOTH empty
# (the default) to generate synthetic reference data in-workspace directly into
# your streaming schema — the self-contained path most forks want.
dbutils.widgets.text("src_catalog", "", "Optional: source catalog to copy seed tables FROM (empty = synthetic)")
dbutils.widgets.text("src_schema", "", "Optional: source schema to copy seed tables FROM (empty = synthetic)")

# COMMAND ----------

params = {
    "uc_catalog": dbutils.widgets.get("uc_catalog"),
    "uc_schema": dbutils.widgets.get("uc_schema"),
    "sql_warehouse_id": dbutils.widgets.get("sql_warehouse_id"),
    "lakebase_catalog": dbutils.widgets.get("lakebase_catalog"),
    "lakebase_schema": dbutils.widgets.get("lakebase_schema"),
    "src_catalog": dbutils.widgets.get("src_catalog"),
    "src_schema": dbutils.widgets.get("src_schema"),
}

missing = [k for k, v in params.items() if k in {"sql_warehouse_id"} and (v is None or v.strip() == "")]
if missing:
    raise ValueError(f"Missing required parameters: {missing}")

display(params)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Repo root (shared by all steps)
# MAGIC
# MAGIC The step notebooks shell out to `scripts/*.py`. Locate the repo root once here
# MAGIC and `chdir` into it so those calls resolve regardless of where the notebook
# MAGIC runs from (Git folder or Workspace Files). `%run ./00_Parameters` shares this
# MAGIC Python session, so `REPO_ROOT` is available to every downstream notebook.

# COMMAND ----------

import os


def _find_repo_root() -> str:
    # 1) Derive from the notebook's own workspace path (Git folder / Workspace Files).
    try:
        ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        nb_path = ctx.notebookPath().get()  # e.g. /Users/me/repo/notebooks/00_Parameters
        candidate = "/Workspace" + os.path.dirname(os.path.dirname(nb_path))
        if os.path.isdir(os.path.join(candidate, "scripts")):
            return candidate
    except Exception:
        pass
    # 2) Fall back to walking up from CWD looking for the repo markers.
    here = os.getcwd()
    for _ in range(8):
        if os.path.isdir(os.path.join(here, "scripts")) and os.path.exists(os.path.join(here, "databricks.yml")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    return os.getcwd()


REPO_ROOT = _find_repo_root()
os.chdir(REPO_ROOT)
print("REPO_ROOT:", REPO_ROOT)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC - Run **01 — Setup Unity Catalog** to create the required schemas/tables for the chosen mode.
