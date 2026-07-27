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

# COMMAND ----------

params = {
    "uc_catalog": dbutils.widgets.get("uc_catalog"),
    "uc_schema": dbutils.widgets.get("uc_schema"),
    "sql_warehouse_id": dbutils.widgets.get("sql_warehouse_id"),
    "lakebase_catalog": dbutils.widgets.get("lakebase_catalog"),
    "lakebase_schema": dbutils.widgets.get("lakebase_schema"),
}

missing = [k for k, v in params.items() if k in {"sql_warehouse_id"} and (v is None or v.strip() == "")]
if missing:
    raise ValueError(f"Missing required parameters: {missing}")

display(params)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC - Run **01 — Setup Unity Catalog** to create the required schemas/tables for the chosen mode.
