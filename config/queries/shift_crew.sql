SELECT
  shift_id,
  operator_id,
  operator_name,
  role,
  assigned_machine,
  skill_level,
  skills,
  status
FROM main.line_manager_ai_kit.shift_crew
WHERE shift_id = :shift_id
ORDER BY role, operator_name;
