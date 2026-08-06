SELECT
  check_id,
  shift_id,
  machine_id,
  timestamp,
  parameter,
  value,
  spec_min,
  spec_max,
  result
FROM main.line_manager_ai_kit.quality_checks_bronze
WHERE shift_id = :shift_id
ORDER BY timestamp DESC;
