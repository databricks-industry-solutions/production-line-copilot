SELECT
  event_id,
  shift_id,
  machine_id,
  event_type,
  severity,
  timestamp_start,
  timestamp_end,
  duration_minutes,
  root_cause,
  description,
  corrective_action,
  resolution_minutes,
  outcome_oee_impact,
  outcome_description
FROM main.line_manager_ai_kit.event_log
WHERE shift_id = :shift_id
ORDER BY timestamp_start DESC;
