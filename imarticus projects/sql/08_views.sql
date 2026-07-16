-- ============================================================
-- 08 — VIEWS  (CREATE VIEW)
-- ============================================================
USE f1_analytics;

-- v_driver_career_summary: one row per driver with headline career stats.
DROP VIEW IF EXISTS v_driver_career_summary;
CREATE VIEW v_driver_career_summary AS
SELECT
  d.driverId,
  CONCAT(d.forename,' ',d.surname)      AS driver,
  d.nationality,
  COUNT(*)                              AS starts,
  SUM(re.positionOrder = 1)             AS wins,
  SUM(re.positionOrder <= 3)            AS podiums,
  SUM(re.grid = 1)                      AS poles,
  ROUND(SUM(re.points), 1)              AS career_points,
  MIN(ra.year)                          AS first_season,
  MAX(ra.year)                          AS last_season,
  ROUND(100 * SUM(re.positionOrder = 1) / NULLIF(COUNT(*),0), 2) AS win_pct
FROM drivers d
JOIN results re ON re.driverId = d.driverId
JOIN races ra   ON ra.raceId  = re.raceId
GROUP BY d.driverId;

-- Demo: the ten winningest drivers, straight from the view.
SELECT driver, nationality, starts, wins, podiums, poles, career_points, win_pct
FROM v_driver_career_summary
ORDER BY wins DESC
LIMIT 10;
