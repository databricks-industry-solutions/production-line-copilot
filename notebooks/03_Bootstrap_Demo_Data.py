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

# MAGIC %pip install --quiet "databricks-sdk>=0.81.0" "databricks-sql-connector>=4.2" psycopg2-binary pyyaml

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %run ./00_Parameters

# COMMAND ----------

import os
import subprocess
import sys

# COMMAND ----------

uc_catalog = dbutils.widgets.get("uc_catalog")
uc_schema = dbutils.widgets.get("uc_schema")
warehouse_id = dbutils.widgets.get("sql_warehouse_id")
src_catalog = dbutils.widgets.get("src_catalog")
src_schema = dbutils.widgets.get("src_schema")

copy_mode = bool(src_catalog.strip()) and bool(src_schema.strip())
print("Target:", f"{uc_catalog}.{uc_schema}")
print("Mode:", f"COPY from {src_catalog}.{src_schema}" if copy_mode else "SYNTHETIC (in-workspace, self-contained)")

# COMMAND ----------

# Default (src_catalog/src_schema empty): generate synthetic reference tables
# in-workspace, directly into the streaming schema. If both src_* widgets are
# set, bootstrap_reference_data.py instead COPIES the seed tables from that
# source schema (see scripts/bootstrap_reference_data.py::_should_copy_from_src).
os.environ["UC_CATALOG"] = uc_catalog
os.environ["UC_SCHEMA"] = uc_schema
os.environ["DATABRICKS_WAREHOUSE_ID"] = warehouse_id
os.environ["SRC_CATALOG"] = src_catalog
os.environ["SRC_SCHEMA"] = src_schema

if "DATABRICKS_HOST" not in os.environ:
    ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
    os.environ["DATABRICKS_HOST"] = ctx.apiUrl().get()

cmd = [sys.executable, os.path.join(REPO_ROOT, "scripts", "bootstrap_reference_data.py")]
print("Running:", " ".join(cmd), "(cwd:", REPO_ROOT, ")")
subprocess.check_call(cmd, cwd=REPO_ROOT)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC - Run **04 — Deploy App** to deploy the Databricks App via Asset Bundles.
