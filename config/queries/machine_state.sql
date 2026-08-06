WITH ranked AS (
  SELECT
    machine_id,
    status,
    ROUND(throughput_per_min, 0) AS throughput_per_min,
    ROUND(speed_pct, 1) AS speed_pct,
    alarm_count,
    ROW_NUMBER() OVER (
      PARTITION BY machine_id
      ORDER BY
        CASE WHEN ts <= :t_current THEN 0 ELSE 1 END,
        CASE WHEN ts <= :t_current THEN ts END DESC,
        CASE WHEN ts > :t_current THEN ts END ASC
    ) AS rn
  FROM main.line_manager_ai_kit.gold_history_machine_state
  WHERE shift_id = :shift_id
)
SELECT machine_id, status, throughput_per_min, speed_pct, alarm_count
FROM ranked
WHERE rn = 1
ORDER BY machine_id;
