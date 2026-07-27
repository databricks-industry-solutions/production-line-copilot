-- Migrate existing UC Delta ``line_kpi_bronze`` and ``line_kpi`` tables to Tier-1 schema
-- (availability / performance / quality / OEE / cumulative_cases).
--
-- Replace ``<CATALOG>`` and ``<SCHEMA>`` and run in Databricks SQL.
-- New deployments: ``scripts/init_realtime_uc.py`` already creates the full column set.

ALTER TABLE <CATALOG>.<SCHEMA>.line_kpi_bronze ADD COLUMNS (
  availability_to_now   DOUBLE,
  performance_to_now   DOUBLE,
  quality_to_now       DOUBLE,
  oee_to_now           DOUBLE,
  cumulative_cases     DOUBLE
);

ALTER TABLE <CATALOG>.<SCHEMA>.line_kpi ADD COLUMNS (
  availability_to_now   DOUBLE,
  performance_to_now   DOUBLE,
  quality_to_now       DOUBLE,
  oee_to_now           DOUBLE,
  cumulative_cases     DOUBLE
);
