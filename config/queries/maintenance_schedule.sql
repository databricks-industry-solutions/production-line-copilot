SELECT
  work_order_id,
  shift_id,
  machine_id,
  maintenance_type,
  planned_start,
  planned_end,
  actual_start,
  actual_end,
  technician,
  status,
  description
FROM main.line_manager_ai_kit.maintenance_schedule
WHERE shift_id = :shift_id
ORDER BY COALESCE(actual_start, planned_start);
