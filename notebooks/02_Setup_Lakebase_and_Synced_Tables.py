# Databricks notebook source
# MAGIC %md
# MAGIC # 02 — Setup Lakebase & Synced Tables (optional but recommended)
# MAGIC
# MAGIC This step enables low-latency operational reads and “real-time control tower” patterns:
# MAGIC
# MAGIC - **Lakebase**: create the `ai_chatbot` schema + chat tables used by the agent server.
# MAGIC - **Synced Tables**: replicate selected UC Delta tables into Lakebase for fast reads.
# MAGIC
# MAGIC **Prerequisites**
# MAGIC - A Lakebase database is created in your workspace.
# MAGIC - Your Databricks App has the Lakebase resource attached (so it gets `PGHOST`, `PGUSER`, etc).
# MAGIC - For Synced Tables, you need the **Lakebase catalog name** (see the Lakebase UI).

# COMMAND ----------

# MAGIC %run ./00_Parameters

# COMMAND ----------

import os
import subprocess

# COMMAND ----------

lakebase_catalog = dbutils.widgets.get("lakebase_catalog")
lakebase_schema = dbutils.widgets.get("lakebase_schema")

uc_catalog = dbutils.widgets.get("uc_catalog")
uc_schema = dbutils.widgets.get("uc_schema")

print("Lakebase catalog:", lakebase_catalog)
print("Lakebase schema:", lakebase_schema)
print("UC:", f"{uc_catalog}.{uc_schema}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2.1 Initialize Lakebase tables used by the app
# MAGIC
# MAGIC This relies on standard `PG*` env vars being present. If you run this in a notebook without those env vars,
# MAGIC set them first (or run the script from the Databricks App runtime).

# COMMAND ----------

cmd = ["python", "scripts/init_lakebase.py"]
print("Running:", " ".join(cmd))
subprocess.check_call(cmd)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2.2 Create Synced Tables (Continuous)

# COMMAND ----------

if not lakebase_catalog.strip():
    raise ValueError("lakebase_catalog is required to create Synced Tables.")

os.environ["LAKEBASE_CATALOG"] = lakebase_catalog
os.environ["LAKEBASE_SCHEMA"] = lakebase_schema
os.environ["UC_CATALOG"] = uc_catalog
os.environ["UC_SCHEMA"] = uc_schema

cmd = ["python", "scripts/setup_synced_tables.py"]
print("Running:", " ".join(cmd))
subprocess.check_call(cmd)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next
# MAGIC - Run **03 — Bootstrap Demo Data** to copy reference tables into your streaming schema.
