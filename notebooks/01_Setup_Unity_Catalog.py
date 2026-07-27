# Databricks notebook source
# MAGIC %md
# MAGIC # 01 — Setup Unity Catalog
# MAGIC
# MAGIC Creates the Unity Catalog schema and live (CDF-enabled) tables used by the **real-time streaming** demo.

# COMMAND ----------

# MAGIC %run ./00_Parameters

# COMMAND ----------

import os
import subprocess

# COMMAND ----------

uc_catalog = dbutils.widgets.get("uc_catalog")
uc_schema = dbutils.widgets.get("uc_schema")

warehouse_id = dbutils.widgets.get("sql_warehouse_id")

print("UC:", f"{uc_catalog}.{uc_schema}")
print("Warehouse:", warehouse_id)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Create streaming tables

# COMMAND ----------

# This script uses DATABRICKS_HOST + DATABRICKS_WAREHOUSE_ID for DBSQL DDL execution.
# In a Databricks notebook, DATABRICKS_HOST is typically available; otherwise set it manually.
os.environ["UC_CATALOG"] = uc_catalog
os.environ["UC_SCHEMA"] = uc_schema
os.environ["DATABRICKS_WAREHOUSE_ID"] = warehouse_id

if "DATABRICKS_HOST" not in os.environ:
    # Best-effort: derive from browser host if present
    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    os.environ["DATABRICKS_HOST"] = ctx.apiUrl().get()

cmd = ["python", "scripts/init_realtime_uc.py"]
print("Running:", " ".join(cmd))
subprocess.check_call(cmd)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC - Run **02 — Setup Lakebase & Synced Tables** (optional, but recommended for “ops-grade” story).
