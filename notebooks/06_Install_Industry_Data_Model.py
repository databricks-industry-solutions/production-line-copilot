# Databricks notebook source
# MAGIC %md
# MAGIC # 06 — Install Industry Data Model (Manufacturing MVM)
# MAGIC
# MAGIC Installs the Databricks **Manufacturing** industry data model (MVM =
# MAGIC *Minimum Viable Model*) into a Unity Catalog catalog of your choice —
# MAGIC catalog, schemas, tables, foreign keys, governance tags, and metric views.
# MAGIC
# MAGIC The model definitions are pulled live from
# MAGIC [`databricks-industry-solutions/lakehouse-industry-data-models`](https://github.com/databricks-industry-solutions/lakehouse-industry-data-models);
# MAGIC this notebook only drives the (serverless-safe) apply loop via
# MAGIC `scripts/install_industry_data_model.py`.
# MAGIC
# MAGIC **Why:** gives the SQL / Genie agents a *governed silver* schema (~98 metric
# MAGIC views) to query, alongside the realtime `ai_serving.*_latest` hot path. The
# MAGIC next notebook / step (`silver_mapper`) populates the model tables from the
# MAGIC simulator's bronze data.

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

# Target catalog for the industry model. Kept SEPARATE from the streaming
# `uc_catalog` so the governed silver model is cleanly isolated from the bronze /
# serving tables.
dbutils.widgets.text("mvm_catalog", "line_manager_silver_mvm", "MVM target catalog")
dbutils.widgets.dropdown("model_size", "mvm", ["mvm", "ecm"], "Model size")
dbutils.widgets.dropdown("install_metric_views", "true", ["true", "false"], "Install metric views")

mvm_catalog = dbutils.widgets.get("mvm_catalog")
model_size = dbutils.widgets.get("model_size")
install_metric_views = dbutils.widgets.get("install_metric_views")
warehouse_id = dbutils.widgets.get("sql_warehouse_id")

print("MVM catalog  :", mvm_catalog)
print("Model size   :", model_size)
print("Metric views :", install_metric_views)
print("Warehouse    :", warehouse_id)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Install the model
# MAGIC
# MAGIC An MVM install typically completes in **tens of minutes** (metric-view
# MAGIC compilation is the long pole). The apply loop is idempotent — re-running is
# MAGIC safe and `already exists` is ignored.

# COMMAND ----------

os.environ["MVM_CATALOG"] = mvm_catalog
os.environ["MODEL_SIZE"] = model_size
os.environ["INDUSTRY"] = "manufacturing"
os.environ["DATABRICKS_WAREHOUSE_ID"] = warehouse_id

if "DATABRICKS_HOST" not in os.environ:
    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    os.environ["DATABRICKS_HOST"] = ctx.apiUrl().get()

cmd = [sys.executable, os.path.join(REPO_ROOT, "scripts", "install_industry_data_model.py")]
if install_metric_views.lower() != "true":
    cmd.append("--no-metrics")
print("Running:", " ".join(cmd), "(cwd:", REPO_ROOT, ")")
subprocess.check_call(cmd, cwd=REPO_ROOT)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC - Populate the model from the simulator bronze data with the **silver
# MAGIC   mapper** (`agent_server/serving/silver_mapper.py`).
# MAGIC - Point Genie / the SQL tools at the MVM metric views (see `docs/EVALUATION.md`).
