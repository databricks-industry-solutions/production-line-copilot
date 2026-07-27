SELECT
  ts,
  shift_id,
  machines_running,
  machines_stopped,
  machines_degraded,
  machines_starved,
  total_throughput,
  cumulative_cases,
  availability_to_now,
  performance_to_now,
  quality_to_now,
  oee_to_now
FROM main.line_manager_ai_kit.line_kpi
WHERE shift_id = :shift_id
ORDER BY ts;
