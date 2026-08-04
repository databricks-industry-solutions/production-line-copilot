# Databricks notebook source
# MAGIC %md
# MAGIC # 07 — Populate Silver Model (sim → Manufacturing MVM)
# MAGIC
# MAGIC Loads the simulator's bronze + reference data into the governed
# MAGIC **Manufacturing MVM** tables installed by notebook 06, so Genie and the SQL
# MAGIC tools can query a standards-aligned silver layer (equipment register,
# MAGIC production line + OEE, downtime failure records, quality inspection results,
# MAGIC material master + stock balance).
# MAGIC
# MAGIC The realtime `ai_serving.*_latest` hot path is untouched — this is an
# MAGIC **analytical refresh** you can re-run any time (idempotent `INSERT OVERWRITE`).

# COMMAND ----------

# MAGIC %pip install --quiet "databricks-sdk>=0.81.0" pyyaml

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./00_Parameters

# COMMAND ----------

import os
import subprocess
import sys

# COMMAND ----------

dbutils.widgets.text("mvm_catalog", "line_manager_silver_mvm", "MVM target catalog")

uc_catalog = dbutils.widgets.get("uc_catalog")
uc_schema = dbutils.widgets.get("uc_schema")
mvm_catalog = dbutils.widgets.get("mvm_catalog")
warehouse_id = dbutils.widgets.get("sql_warehouse_id")

print("Source :", f"{uc_catalog}.{uc_schema}")
print("MVM    :", mvm_catalog)
print("Warehouse:", warehouse_id)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Refresh the silver model

# COMMAND ----------

os.environ["MVM_CATALOG"] = mvm_catalog
os.environ["UC_CATALOG"] = uc_catalog
os.environ["UC_SCHEMA"] = uc_schema
os.environ["DATABRICKS_WAREHOUSE_ID"] = warehouse_id

if "DATABRICKS_HOST" not in os.environ:
    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    os.environ["DATABRICKS_HOST"] = ctx.apiUrl().get()

# Run as a module so the ``agent_server`` package resolves from REPO_ROOT.
cmd = [sys.executable, "-m", "agent_server.serving.silver_mapper"]
print("Running:", " ".join(cmd), "(cwd:", REPO_ROOT, ")")
subprocess.check_call(cmd, cwd=REPO_ROOT)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC - Point Genie / SQL tools at the MVM metric views (Theme 1 ID4).
# MAGIC - Ask the line questions like *"OEE by machine this shift"* against the
# MAGIC   governed model.
