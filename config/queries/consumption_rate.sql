SELECT material_id,
       SUM(quantity_consumed) AS total_consumed,
       COUNT(*) AS samples,
       ROUND(
         SUM(quantity_consumed) / NULLIF(
           (UNIX_TIMESTAMP(MAX(timestamp)) - UNIX_TIMESTAMP(MIN(timestamp))) / 3600.0,
           0
         ),
         2
       ) AS avg_rate_per_hour
FROM main.line_manager_ai_kit.consumption_log
WHERE shift_id = :shift_id
GROUP BY material_id
ORDER BY material_id;
