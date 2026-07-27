SELECT
  shift_id,
  sku,
  sku_name,
  planned_cases,
  actual_cases,
  ideal_rate_bpm,
  start_time,
  end_time,
  status,
  batch_id
FROM main.line_manager_ai_kit.production_schedule
WHERE shift_id = :shift_id
ORDER BY start_time;
