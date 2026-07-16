-- ============================================================
-- TIER 3 — ADVANCED
-- Window functions / CTEs / LAG / running totals / frames / NTILE
-- ============================================================
USE f1_analytics;

-- Q1. Career wins ranked: RANK vs DENSE_RANK (see the tie behaviour differ).
WITH w AS (
  SELECT driverId, SUM(positionOrder = 1) AS wins
  FROM results GROUP BY driverId
)
SELECT CONCAT(d.forename,' ',d.surname) AS driver, w.wins,
       RANK()       OVER (ORDER BY w.wins DESC) AS rank_pos,
       DENSE_RANK() OVER (ORDER BY w.wins DESC) AS dense_rank_pos
FROM w JOIN drivers d ON d.driverId = w.driverId
WHERE w.wins > 0
ORDER BY w.wins DESC
LIMIT 20;

-- Q2. Verstappen 2023 cumulative points progression (running total).
SELECT ra.round, ra.name, re.points,
       SUM(re.points) OVER (ORDER BY ra.round) AS running_total
FROM results re
JOIN races ra  ON ra.raceId = re.raceId
JOIN drivers d ON d.driverId = re.driverId
WHERE ra.year = 2023 AND d.driverRef = 'max_verstappen'
ORDER BY ra.round;

-- Q3. Second-most successful driver (by wins) per nationality.
WITH w AS (
  SELECT d.driverId, d.nationality, CONCAT(d.forename,' ',d.surname) AS driver,
         SUM(re.positionOrder = 1) AS wins
  FROM drivers d JOIN results re ON re.driverId = d.driverId
  GROUP BY d.driverId
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY nationality ORDER BY wins DESC) AS rn
  FROM w WHERE wins > 0
)
SELECT nationality, driver, wins
FROM ranked WHERE rn = 2
ORDER BY wins DESC
LIMIT 20;

-- Q4. Lap-to-lap time delta for Verstappen in the 2023 Abu Dhabi GP (LAG).
SELECT lt.lap, lt.time,
       lt.milliseconds - LAG(lt.milliseconds) OVER (ORDER BY lt.lap) AS delta_ms
FROM lap_times lt
JOIN races ra ON ra.raceId = lt.raceId
JOIN drivers d ON d.driverId = lt.driverId
WHERE ra.year = 2023
  AND ra.name = 'Abu Dhabi Grand Prix'
  AND d.driverRef = 'max_verstappen'
ORDER BY lt.lap
LIMIT 25;

-- Q5. Most improved on Sunday 2023: average positions gained grid -> finish.
SELECT CONCAT(d.forename,' ',d.surname) AS driver,
       ROUND(AVG(re.grid - re.positionOrder), 2) AS avg_positions_gained
FROM results re
JOIN races ra  ON ra.raceId = re.raceId
JOIN drivers d ON d.driverId = re.driverId
WHERE ra.year = 2023 AND re.grid > 0
GROUP BY d.driverId
HAVING COUNT(*) >= 5
ORDER BY avg_positions_gained DESC
LIMIT 10;

-- Q6. 3-race rolling average of points for the 2021 champion (window frame).
SELECT ra.round, ra.name, re.points,
       ROUND(AVG(re.points) OVER (ORDER BY ra.round
              ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS rolling_avg_3
FROM results re
JOIN races ra  ON ra.raceId = re.raceId
JOIN drivers d ON d.driverId = re.driverId
WHERE ra.year = 2021 AND d.driverRef = 'max_verstappen'
ORDER BY ra.round;

-- Q7. Longest streak of consecutive podium finishes (gaps-and-islands).
WITH seq AS (
  SELECT re.driverId,
         ROW_NUMBER() OVER (PARTITION BY re.driverId ORDER BY ra.date, ra.round) AS rn,
         (re.positionOrder <= 3) AS is_podium
  FROM results re JOIN races ra ON ra.raceId = re.raceId
),
grp AS (
  SELECT driverId, is_podium,
         rn - ROW_NUMBER() OVER (PARTITION BY driverId, is_podium ORDER BY rn) AS island
  FROM seq
)
SELECT CONCAT(d.forename,' ',d.surname) AS driver, COUNT(*) AS podium_streak
FROM grp JOIN drivers d ON d.driverId = grp.driverId
WHERE is_podium = 1
GROUP BY grp.driverId, grp.island
ORDER BY podium_streak DESC
LIMIT 10;

-- Q8. 2023 Abu Dhabi GP: each driver's best lap vs the race's fastest lap.
WITH best AS (
  SELECT driverId, MIN(milliseconds) AS best_ms
  FROM lap_times
  WHERE raceId = (
    SELECT raceId
    FROM races
    WHERE year = 2023 AND name = 'Abu Dhabi Grand Prix'
  )
  GROUP BY driverId
)
SELECT CONCAT(d.forename,' ',d.surname) AS driver,
       SEC_TO_TIME(best_ms/1000) AS best_lap,
       best_ms - MIN(best_ms) OVER () AS gap_to_fastest_ms
FROM best JOIN drivers d ON d.driverId = best.driverId
ORDER BY best_ms
LIMIT 15;

-- Q9. Career quartiles by points-per-season (NTILE).
WITH pps AS (
  SELECT re.driverId,
         SUM(re.points) / COUNT(DISTINCT ra.year) AS pts_per_season
  FROM results re JOIN races ra ON ra.raceId = re.raceId
  GROUP BY re.driverId
  HAVING SUM(re.points) > 0
)
SELECT CONCAT(d.forename,' ',d.surname) AS driver,
       ROUND(pts_per_season, 1) AS pts_per_season,
       NTILE(4) OVER (ORDER BY pts_per_season DESC) AS career_quartile
FROM pps JOIN drivers d ON d.driverId = pps.driverId
ORDER BY pts_per_season DESC
LIMIT 20;

-- Q10. Drivers who won within their first 10 career starts.
WITH career AS (
  SELECT re.driverId, re.positionOrder,
         ROW_NUMBER() OVER (PARTITION BY re.driverId ORDER BY ra.date, ra.round) AS start_no
  FROM results re JOIN races ra ON ra.raceId = re.raceId
)
SELECT CONCAT(d.forename,' ',d.surname) AS driver, MIN(start_no) AS first_win_start
FROM career c JOIN drivers d ON d.driverId = c.driverId
WHERE c.positionOrder = 1 AND c.start_no <= 10
GROUP BY c.driverId
ORDER BY first_win_start
LIMIT 20;
