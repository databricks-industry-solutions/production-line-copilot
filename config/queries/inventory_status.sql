SELECT
  material_id,
  shift_id,
  timestamp,
  on_hand_qty,
  safety_stock,
  reorder_point,
  status
FROM main.line_manager_ai_kit.inventory_status
WHERE shift_id = :shift_id
ORDER BY
  CASE status WHEN 'CRITICAL' THEN 1 WHEN 'LOW' THEN 2 ELSE 3 END,
  material_id;
