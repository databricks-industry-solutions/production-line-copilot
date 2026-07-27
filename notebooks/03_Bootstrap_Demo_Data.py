# Databricks notebook source
# MAGIC %md
# MAGIC # 03 — Bootstrap Demo Data
# MAGIC
# MAGIC This step prepares the data needed by the UI + agent context.
# MAGIC
# MAGIC By **default** (recommended) this generates synthetic reference tables **in-workspace**
# MAGIC directly into your **streaming** schema (default: `main.line_manager_streaming`) — no external
# MAGIC seed schema required. Only if you set `src_catalog`/`src_schema` in `project-settings`
# MAGIC does it instead **copy** reference tables from that source schema. Leave them empty for the
# MAGIC self-contained path.

# COMMAND ----------

# MAGIC %run ./00_Parameters

# COMMAND ----------

import os
import subprocess

# COMMAND ----------

uc_catalog = dbutils.widgets.get("uc_catalog")
uc_schema = dbutils.widgets.get("uc_schema")
warehouse_id = dbutils.widgets.get("sql_warehouse_id")

print("Target:", f"{uc_catalog}.{uc_schema}")

# COMMAND ----------

# Copy reference/dimension tables from main.line_manager_ai_kit -> chosen streaming schema
os.environ["UC_CATALOG"] = uc_catalog
os.environ["UC_SCHEMA"] = uc_schema
os.environ["DATABRICKS_WAREHOUSE_ID"] = warehouse_id

if "DATABRICKS_HOST" not in os.environ:
    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    os.environ["DATABRICKS_HOST"] = ctx.apiUrl().get()

cmd = ["python", "scripts/bootstrap_reference_data.py"]
print("Running:", " ".join(cmd))
subprocess.check_call(cmd)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC - Run **04 — Deploy App** to deploy the Databricks App via Asset Bundles.
