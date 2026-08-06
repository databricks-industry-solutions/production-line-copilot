-- Shift events with their outcomes.
--
-- The outcome columns are written to event_log_bronze but dropped by the
-- quality gate on the way to gold, so selecting them from gold alone returns
-- a past problem with no resolution. The join brings them back — the same
-- shape as agent_server/tools/similar_case_retriever.py.
SELECT
  e.event_id,
  e.shift_id,
  e.machine_id,
  e.event_type,
  e.severity,
  e.timestamp_start,
  e.timestamp_end,
  e.duration_minutes,
  e.root_cause,
  e.description,
  e.corrective_action,
  b.resolution_minutes,
  b.outcome_oee_impact,
  b.outcome_description
FROM main.line_manager_ai_kit.gold_history_event_log e
LEFT JOIN main.line_manager_ai_kit.event_log_bronze b
  ON b.event_id = e.event_id
WHERE e.shift_id = :shift_id
ORDER BY e.timestamp_start DESC;
