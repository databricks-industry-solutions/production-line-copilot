SELECT
  ts,
  machine_id,
  status,
  throughput_per_min,
  speed_pct,
  counter_delta,
  primary_sensor_1,
  primary_sensor_2,
  material_level_pct,
  alarm_count
FROM main.line_manager_ai_kit.machine_state
WHERE shift_id = :shift_id
  AND machine_id = :machine_id
ORDER BY ts;
