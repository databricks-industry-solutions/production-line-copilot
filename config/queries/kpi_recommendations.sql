SELECT
  recommendation_id,
  category,
  title,
  description,
  priority,
  estimated_impact
FROM main.line_manager_ai_kit.kpi_recommendations
ORDER BY
  CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
  category;
