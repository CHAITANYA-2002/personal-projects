-- ============================================================
-- TIER 4 — EXPERT
-- Recursive CTE / gaps-and-islands / CREATE INDEX / EXPLAIN ANALYZE
-- (Views and procedures live in 08 and 09.)
-- ============================================================
USE f1_analytics;

-- Q1. Alonso's season-by-season team timeline, flagging each switch (LAG).
WITH team_year AS (
  SELECT ra.year, re.constructorId,
         ROW_NUMBER() OVER (
           PARTITION BY ra.year
           ORDER BY COUNT(*) DESC, re.constructorId
         ) AS rn
  FROM results re JOIN races ra ON ra.raceId = re.raceId
  WHERE re.driverId = (SELECT driverId FROM drivers WHERE driverRef = 'alonso')
  GROUP BY ra.year, re.constructorId
)
SELECT ty.year, c.name AS team,
       CASE WHEN c.name <> LAG(c.name) OVER (ORDER BY ty.year) THEN '<< switched' ELSE '' END AS note
FROM team_year ty
JOIN constructors c ON c.constructorId = ty.constructorId
WHERE ty.rn = 1
ORDER BY ty.year;

-- Q2. Teammate graph: drivers reachable within 3 "teammate degrees"
--     of Lewis Hamilton (RECURSIVE CTE). Teammates share a raceId+constructorId.
WITH RECURSIVE pairs AS (
  SELECT DISTINCT a.driverId AS d1, b.driverId AS d2
  FROM results a
  JOIN results b ON b.raceId = a.raceId AND b.constructorId = a.constructorId
                AND a.driverId <> b.driverId
),
reach AS (
  SELECT (SELECT driverId FROM drivers WHERE driverRef = 'hamilton') AS driverId, 0 AS degree
  UNION
  SELECT p.d2, r.degree + 1
  FROM reach r
  JOIN pairs p ON p.d1 = r.driverId
  WHERE r.degree < 3
)
SELECT degree, COUNT(DISTINCT driverId) AS drivers_reached
FROM reach
GROUP BY degree
ORDER BY degree;

-- Q3. Championship margin (final points, P1 vs P2) per season.
WITH final_race AS (
  SELECT year, raceId, ROW_NUMBER() OVER (PARTITION BY year ORDER BY round DESC) AS rr
  FROM races
)
SELECT fr.year,
       MAX(CASE WHEN ds.position = 1 THEN ds.points END) AS champion_pts,
       MAX(CASE WHEN ds.position = 2 THEN ds.points END) AS runnerup_pts,
       MAX(CASE WHEN ds.position = 1 THEN ds.points END)
     - MAX(CASE WHEN ds.position = 2 THEN ds.points END) AS margin
FROM final_race fr
JOIN driver_standings ds ON ds.raceId = fr.raceId
WHERE fr.rr = 1
GROUP BY fr.year
ORDER BY fr.year DESC
LIMIT 20;

-- Q4. "Championship decider" round per season (2010+): earliest round after
--     which the leader's gap exceeded the points still available.
--     ponytail: naive 25 pts/race ceiling; pre-2010 points systems differ.
WITH standing AS (
  SELECT ra.year, ra.round, ra.raceId, ds.points,
         ROW_NUMBER() OVER (PARTITION BY ra.raceId ORDER BY ds.points DESC) AS pos,
         MAX(ra.round) OVER (PARTITION BY ra.year) AS total_rounds
  FROM driver_standings ds JOIN races ra ON ra.raceId = ds.raceId
  WHERE ra.year >= 2010
),
title_lead AS (
  SELECT year, round, total_rounds,
         MAX(CASE WHEN pos = 1 THEN points END) AS p1,
         MAX(CASE WHEN pos = 2 THEN points END) AS p2
  FROM standing
  GROUP BY year, round, total_rounds
)
SELECT year, MIN(round) AS title_decided_round, MAX(total_rounds) AS season_rounds
FROM title_lead
WHERE (p1 - p2) > 25 * (total_rounds - round)
GROUP BY year
ORDER BY year;

-- Q5. Longest streak of consecutive constructors' titles (gaps-and-islands).
WITH final_race AS (
  SELECT year, raceId, ROW_NUMBER() OVER (PARTITION BY year ORDER BY round DESC) AS rr
  FROM races
),
champ AS (
  SELECT fr.year, cs.constructorId
  FROM final_race fr
  JOIN constructor_standings cs ON cs.raceId = fr.raceId
  WHERE fr.rr = 1 AND cs.position = 1
),
seq AS (
  SELECT year, constructorId,
         year - ROW_NUMBER() OVER (PARTITION BY constructorId ORDER BY year) AS island
  FROM champ
)
SELECT c.name, COUNT(*) AS consecutive_titles, MIN(year) AS from_year, MAX(year) AS to_year
FROM seq JOIN constructors c ON c.constructorId = seq.constructorId
GROUP BY seq.constructorId, seq.island
ORDER BY consecutive_titles DESC
LIMIT 10;

-- Q6. Champions who won the title by 1 point or less.
WITH final_race AS (
  SELECT year, raceId, ROW_NUMBER() OVER (PARTITION BY year ORDER BY round DESC) AS rr
  FROM races
),
margins AS (
  SELECT fr.year,
         MAX(CASE WHEN ds.position = 1 THEN ds.driverId END) AS champ_id,
         MAX(CASE WHEN ds.position = 1 THEN ds.points END)
       - MAX(CASE WHEN ds.position = 2 THEN ds.points END) AS margin
  FROM final_race fr
  JOIN driver_standings ds ON ds.raceId = fr.raceId
  WHERE fr.rr = 1
  GROUP BY fr.year
)
SELECT m.year, CONCAT(d.forename,' ',d.surname) AS champion, m.margin
FROM margins m JOIN drivers d ON d.driverId = m.champ_id
WHERE m.margin <= 1
ORDER BY m.margin, m.year;

-- Q7. Hamilton vs Schumacher, head-to-head at the same career stage
--     (cumulative wins after race N of each career). Driver references are
--     resolved from the drivers table instead of relying on source IDs.
WITH career AS (
  SELECT d.driverRef, re.positionOrder,
         ROW_NUMBER() OVER (PARTITION BY re.driverId ORDER BY ra.date, ra.round) AS race_no
  FROM results re JOIN races ra ON ra.raceId = re.raceId
  JOIN drivers d ON d.driverId = re.driverId
  WHERE d.driverRef IN ('hamilton', 'michael_schumacher')
),
cum AS (
  SELECT driverRef, race_no,
         SUM(positionOrder = 1) OVER (PARTITION BY driverRef ORDER BY race_no) AS cum_wins
  FROM career
)
SELECT race_no,
       MAX(CASE WHEN driverRef = 'hamilton' THEN cum_wins END) AS hamilton_wins,
       MAX(CASE WHEN driverRef = 'michael_schumacher' THEN cum_wins END) AS schumacher_wins
FROM cum
GROUP BY race_no
HAVING hamilton_wins IS NOT NULL AND schumacher_wins IS NOT NULL
ORDER BY race_no
LIMIT 30;

-- Q8. PERFORMANCE — index on a non-indexed column, with EXPLAIN ANALYZE.
--     Run each EXPLAIN, note the plan (full scan -> index lookup), save to
--     results/screenshots/. FK columns are already indexed, so we target `grid`.
EXPLAIN ANALYZE
SELECT AVG(positionOrder) FROM results WHERE grid = 1;

-- (re-runnable) drop the index first if a previous run already created it
SET @drop := IF(
  (SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = 'f1_analytics' AND table_name = 'results'
      AND index_name = 'idx_results_grid') > 0,
  'DROP INDEX idx_results_grid ON results', 'DO 0');
PREPARE s FROM @drop; EXECUTE s; DEALLOCATE PREPARE s;

CREATE INDEX idx_results_grid ON results(grid);

EXPLAIN ANALYZE
SELECT AVG(positionOrder) FROM results WHERE grid = 1;
