SELECT
  shift_id,
  scenario_name,
  scenario_type,
  description,
  shift_start,
  shift_end,
  default_moment,
  key_machines
FROM main.line_manager_ai_kit.scenario_index
ORDER BY shift_id;
