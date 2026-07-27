SELECT
  timestamp,
  machine_id,
  sensor_name,
  value
FROM main.line_manager_ai_kit.machine_telemetry
WHERE shift_id = :shift_id
  AND machine_id = :machine_id
  AND timestamp BETWEEN :t_start AND :t_end
ORDER BY timestamp, sensor_name;
