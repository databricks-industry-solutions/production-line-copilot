# Databricks notebook source
# MAGIC %md
# MAGIC # 05 — Validate
# MAGIC
# MAGIC Quick checks to confirm the accelerator is correctly installed.

# COMMAND ----------

# MAGIC %run ./00_Parameters

# COMMAND ----------

uc_catalog = dbutils.widgets.get("uc_catalog")
uc_schema = dbutils.widgets.get("uc_schema")

tables_to_check = [
    "machine_state",
    "line_kpi",
    "event_log",
]

for t in tables_to_check:
    fqn = f"{uc_catalog}.{uc_schema}.{t}"
    try:
        df = spark.sql(f"SELECT COUNT(*) AS cnt FROM {fqn}")
        cnt = df.collect()[0]["cnt"]
        print(f"OK: {fqn} ({cnt} rows)")
    except Exception as e:
        print(f"ERROR: {fqn}: {e}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## App checks
# MAGIC - Open the Databricks App URL.
# MAGIC - Confirm **Control Tower** loads and KPIs render.
# MAGIC - Trigger an incident (e.g. **Filler breakdown**) and confirm cascade updates.
# MAGIC - Ask the assistant a question and confirm you see a streaming response.
