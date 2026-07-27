SELECT
  shift_id,
  moment_id,
  label,
  timestamp,
  description,
  highlight_machines
FROM main.line_manager_ai_kit.scenario_moments
WHERE shift_id = :shift_id
ORDER BY timestamp;
