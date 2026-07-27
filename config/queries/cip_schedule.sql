SELECT
  cip_id,
  shift_id,
  machine_id,
  planned_start,
  planned_end,
  actual_start,
  actual_end,
  cip_type,
  chemical_used,
  duration_minutes,
  status
FROM main.line_manager_ai_kit.cip_schedule
WHERE shift_id = :shift_id
ORDER BY planned_start;
