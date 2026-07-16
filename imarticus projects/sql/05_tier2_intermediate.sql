-- ============================================================
-- TIER 2 — INTERMEDIATE
-- JOINs / subqueries / CASE / UNION / date arithmetic / COALESCE / NULLIF
-- Winner = positionOrder = 1 (position is NULL for DNFs).
-- Pole start = grid = 1 (covers all eras; qualifying table starts 1994).
-- ============================================================
USE f1_analytics;

-- Q1. 2023 race winners: driver, constructor, race.
SELECT ra.round, ra.name AS race,
       CONCAT(d.forename,' ',d.surname) AS winner,
       c.name AS constructor
FROM results re
JOIN races ra        ON ra.raceId = re.raceId
JOIN drivers d       ON d.driverId = re.driverId
JOIN constructors c  ON c.constructorId = re.constructorId
WHERE ra.year = 2023 AND re.positionOrder = 1
ORDER BY ra.round;

-- Q2. Drivers who won a race but NEVER started from pole (grid = 1).
SELECT CONCAT(d.forename,' ',d.surname) AS driver, COUNT(*) AS wins
FROM results re
JOIN drivers d ON d.driverId = re.driverId
WHERE re.positionOrder = 1
  AND d.driverId NOT IN (SELECT driverId FROM results WHERE grid = 1)
GROUP BY d.driverId
ORDER BY wins DESC;

-- Q3. Career win % (min 20 starts). NULLIF guards divide-by-zero.
SELECT CONCAT(d.forename,' ',d.surname) AS driver,
       COUNT(*) AS starts,
       SUM(re.positionOrder = 1) AS wins,
       ROUND(100 * SUM(re.positionOrder = 1) / NULLIF(COUNT(*),0), 2) AS win_pct
FROM results re
JOIN drivers d ON d.driverId = re.driverId
GROUP BY d.driverId
HAVING starts >= 20
ORDER BY win_pct DESC
LIMIT 15;

-- Q4. Age category at debut race (CASE + date arithmetic).
WITH debut AS (
  SELECT re.driverId, MIN(ra.date) AS debut_date
  FROM results re JOIN races ra ON ra.raceId = re.raceId
  GROUP BY re.driverId
)
SELECT
  CASE
    WHEN TIMESTAMPDIFF(YEAR, d.dob, deb.debut_date) < 20 THEN 'Prodigy (<20)'
    WHEN TIMESTAMPDIFF(YEAR, d.dob, deb.debut_date) <= 30 THEN 'Standard (20-30)'
    ELSE 'Veteran (>30)'
  END AS debut_category,
  COUNT(*) AS drivers
FROM debut deb
JOIN drivers d ON d.driverId = deb.driverId
WHERE d.dob IS NOT NULL
GROUP BY debut_category
ORDER BY drivers DESC;

-- Q5. 2023 races where the pole-sitter (grid 1) did NOT win.
SELECT ra.round, ra.name,
       CONCAT(dp.forename,' ',dp.surname) AS started_pole,
       CONCAT(dw.forename,' ',dw.surname) AS winner
FROM races ra
JOIN results po ON po.raceId = ra.raceId AND po.grid = 1
JOIN results wi ON wi.raceId = ra.raceId AND wi.positionOrder = 1
JOIN drivers dp ON dp.driverId = po.driverId
JOIN drivers dw ON dw.driverId = wi.driverId
WHERE ra.year = 2023 AND po.driverId <> wi.driverId
ORDER BY ra.round;

-- Q6. Constructors with the most 1-2 finishes (both cars top two).
SELECT c.name, COUNT(*) AS one_two_finishes
FROM results a
JOIN results b ON b.raceId = a.raceId AND b.constructorId = a.constructorId
              AND a.positionOrder = 1 AND b.positionOrder = 2
JOIN constructors c ON c.constructorId = a.constructorId
GROUP BY c.constructorId
ORDER BY one_two_finishes DESC
LIMIT 10;

-- Q7. Unified 2023 points ledger: race + sprint combined (UNION ALL).
WITH ledger AS (
  SELECT re.driverId, re.points FROM results re JOIN races ra ON ra.raceId=re.raceId WHERE ra.year=2023
  UNION ALL
  SELECT sr.driverId, sr.points FROM sprint_results sr JOIN races ra ON ra.raceId=sr.raceId WHERE ra.year=2023
)
SELECT CONCAT(d.forename,' ',d.surname) AS driver, ROUND(SUM(l.points),1) AS total_2023
FROM ledger l JOIN drivers d ON d.driverId = l.driverId
GROUP BY d.driverId
ORDER BY total_2023 DESC
LIMIT 10;

-- Q8. Longest races by the winner's total race time (COALESCE for safety).
SELECT ra.year, ra.name,
       SEC_TO_TIME(FLOOR(re.milliseconds/1000)) AS winner_time
FROM results re
JOIN races ra ON ra.raceId = re.raceId
WHERE re.positionOrder = 1 AND re.milliseconds IS NOT NULL
ORDER BY re.milliseconds DESC
LIMIT 10;

-- Q9. Every 2022 driver's points, INCLUDING zero-scorers (LEFT JOIN + COALESCE).
WITH d2022 AS (
  SELECT DISTINCT re.driverId
  FROM results re JOIN races ra ON ra.raceId = re.raceId
  WHERE ra.year = 2022
)
SELECT CONCAT(dr.forename,' ',dr.surname) AS driver,
       COALESCE(SUM(re.points),0) AS points_2022
FROM d2022 x
JOIN drivers dr ON dr.driverId = x.driverId
LEFT JOIN results re ON re.driverId = x.driverId
     AND re.raceId IN (SELECT raceId FROM races WHERE year = 2022)
GROUP BY dr.driverId
ORDER BY points_2022 DESC;

-- Q10. Distinct constructors per race-winning driver (COUNT DISTINCT).
SELECT CONCAT(d.forename,' ',d.surname) AS driver,
       COUNT(DISTINCT re.constructorId) AS constructors_driven,
       SUM(re.positionOrder = 1) AS career_wins
FROM results re
JOIN drivers d ON d.driverId = re.driverId
GROUP BY d.driverId
HAVING career_wins >= 1
ORDER BY constructors_driven DESC, career_wins DESC
LIMIT 15;
