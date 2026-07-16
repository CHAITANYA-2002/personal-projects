-- ============================================================
-- CAPSTONE — "WHAT MAKES A CHAMPION?"
-- Each query answers one thesis question. Champion = P1 in the drivers'
-- standings at a season's final round. DNF = classified position IS NULL.
-- ============================================================
USE f1_analytics;

-- C1. CONSISTENCY vs PEAK — champions vs runners-up: avg wins & DNFs/season.
WITH final_race AS (
  SELECT year, raceId, ROW_NUMBER() OVER (PARTITION BY year ORDER BY round DESC) AS rr FROM races
),
top2 AS (
  SELECT fr.year, ds.driverId, ds.position
  FROM final_race fr JOIN driver_standings ds ON ds.raceId = fr.raceId
  WHERE fr.rr = 1 AND ds.position IN (1,2)
),
season_stats AS (
  SELECT ra.year, re.driverId,
         SUM(re.positionOrder = 1) AS wins,
         SUM(re.position IS NULL)  AS dnfs,
         COUNT(*)                  AS races
  FROM results re JOIN races ra ON ra.raceId = re.raceId
  GROUP BY ra.year, re.driverId
)
SELECT CASE t.position WHEN 1 THEN '1. Champion' ELSE '2. Runner-up' END AS finishing,
       ROUND(AVG(ss.wins),2) AS avg_wins_per_season,
       ROUND(AVG(ss.dnfs),2) AS avg_dnfs_per_season,
       ROUND(100*AVG(ss.wins/NULLIF(ss.races,0)),1) AS avg_win_rate_pct
FROM top2 t
JOIN season_stats ss ON ss.year = t.year AND ss.driverId = t.driverId
GROUP BY t.position;

-- C2. AGE CURVE — average points per race by driver age.
SELECT TIMESTAMPDIFF(YEAR, d.dob, ra.date) AS age,
       COUNT(*) AS races,
       ROUND(AVG(re.points),3) AS avg_points_per_race
FROM results re
JOIN races ra  ON ra.raceId = re.raceId
JOIN drivers d ON d.driverId = re.driverId
WHERE d.dob IS NOT NULL
GROUP BY age
HAVING races >= 100
ORDER BY age;

-- C3. CAR vs DRIVER — how many champions won races for more than one team?
WITH final_race AS (
  SELECT year, raceId, ROW_NUMBER() OVER (PARTITION BY year ORDER BY round DESC) AS rr FROM races
),
champs AS (
  SELECT DISTINCT ds.driverId
  FROM final_race fr JOIN driver_standings ds ON ds.raceId = fr.raceId
  WHERE fr.rr = 1 AND ds.position = 1
),
teams AS (
  SELECT c.driverId, COUNT(DISTINCT re.constructorId) AS winning_teams
  FROM champs c
  JOIN results re ON re.driverId = c.driverId AND re.positionOrder = 1
  GROUP BY c.driverId
)
SELECT COUNT(*) AS total_champions_with_wins,
       SUM(winning_teams > 1) AS won_with_multiple_teams,
       ROUND(100*SUM(winning_teams > 1)/COUNT(*),1) AS pct_multi_team
FROM teams;

-- C4. QUALIFYING — Pearson correlation between a driver-season's average grid
--     and its total points (MySQL has no corr(); computed by hand).
WITH ds AS (
  SELECT ra.year, re.driverId,
         AVG(NULLIF(re.grid,0)) AS avg_grid,
         SUM(re.points)         AS pts
  FROM results re JOIN races ra ON ra.raceId = re.raceId
  GROUP BY ra.year, re.driverId
  HAVING avg_grid IS NOT NULL
)
SELECT COUNT(*) AS driver_seasons,
       ROUND(
         (COUNT(*)*SUM(avg_grid*pts) - SUM(avg_grid)*SUM(pts)) /
         NULLIF(SQRT(
           (COUNT(*)*SUM(avg_grid*avg_grid) - POW(SUM(avg_grid),2)) *
           (COUNT(*)*SUM(pts*pts)           - POW(SUM(pts),2))
         ), 0)
       , 4) AS corr_avggrid_vs_points
FROM ds;

-- C5. RELIABILITY — mechanical-DNF rate: champions vs the rest of the field.
--     Mechanical = a curated set of machine-failure status codes.
WITH final_race AS (
  SELECT year, raceId, ROW_NUMBER() OVER (PARTITION BY year ORDER BY round DESC) AS rr FROM races
),
champs AS (
  SELECT fr.year, ds.driverId
  FROM final_race fr JOIN driver_standings ds ON ds.raceId = fr.raceId
  WHERE fr.rr = 1 AND ds.position = 1
),
mech AS (
  SELECT ra.year, re.driverId,
         SUM(s.status IN ('Engine','Gearbox','Transmission','Hydraulics','Electrical',
             'Suspension','Brakes','Clutch','Overheating','Power Unit','Fuel system',
             'Oil leak','Water leak','Driveshaft','Radiator','Throttle','Turbo','Exhaust',
             'Differential','Alternator','Fuel pump','Ignition','Oil pressure','Wheel',
             'Halfshaft','Mechanical','Power loss','Fuel leak','Fuel pressure',
             'Cooling system','Vibrations','ERS','Battery','Distributor','Pneumatics',
             'Engine fire','Spark plugs','Wheel bearing','Oil line')) AS mech_dnf,
         COUNT(*) AS races
  FROM results re
  JOIN races ra ON ra.raceId = re.raceId
  JOIN status s ON s.statusId = re.statusId
  GROUP BY ra.year, re.driverId
)
SELECT CASE WHEN c.driverId IS NOT NULL THEN 'Champion' ELSE 'Rest of field' END AS grp,
       SUM(m.mech_dnf) AS mechanical_dnfs,
       SUM(m.races)    AS races,
       ROUND(100*SUM(m.mech_dnf)/NULLIF(SUM(m.races),0),2) AS mech_dnf_pct
FROM mech m
LEFT JOIN champs c ON c.year = m.year AND c.driverId = m.driverId
GROUP BY grp;
