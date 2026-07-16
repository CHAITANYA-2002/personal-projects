-- ============================================================
-- 03 — DATA PROFILING / VERIFICATION
-- Prove the load is clean before analysing.
--   mysql -u root -proot f1_analytics < 03_data_profiling.sql
-- ============================================================
USE f1_analytics;

-- P1. Row count per table.
SELECT 'circuits' AS table_name, COUNT(*) AS n_rows FROM circuits UNION ALL
SELECT 'constructors', COUNT(*) FROM constructors UNION ALL
SELECT 'drivers', COUNT(*) FROM drivers UNION ALL
SELECT 'seasons', COUNT(*) FROM seasons UNION ALL
SELECT 'status', COUNT(*) FROM status UNION ALL
SELECT 'races', COUNT(*) FROM races UNION ALL
SELECT 'qualifying', COUNT(*) FROM qualifying UNION ALL
SELECT 'pit_stops', COUNT(*) FROM pit_stops UNION ALL
SELECT 'lap_times', COUNT(*) FROM lap_times UNION ALL
SELECT 'results', COUNT(*) FROM results UNION ALL
SELECT 'sprint_results', COUNT(*) FROM sprint_results UNION ALL
SELECT 'driver_standings', COUNT(*) FROM driver_standings UNION ALL
SELECT 'constructor_standings', COUNT(*) FROM constructor_standings UNION ALL
SELECT 'constructor_results', COUNT(*) FROM constructor_results;

-- P2. NULL audit on columns the analysis depends on.
SELECT
  SUM(position IS NULL)     AS results_null_position,   -- expected > 0 (DNFs)
  SUM(milliseconds IS NULL) AS results_null_ms,
  SUM(grid = 0)             AS results_grid_zero        -- pit-lane / no grid
FROM results;
SELECT SUM(dob IS NULL) AS drivers_null_dob FROM drivers;

-- P3. Referential integrity — every declared child key must have a parent (expect 0).
SELECT
  (SELECT COUNT(*) FROM races r LEFT JOIN seasons x ON x.year=r.year WHERE x.year IS NULL) AS races_orphan_season,
  (SELECT COUNT(*) FROM races r LEFT JOIN circuits x ON x.circuitId=r.circuitId WHERE x.circuitId IS NULL) AS races_orphan_circuit,
  (SELECT COUNT(*) FROM qualifying q LEFT JOIN races x ON x.raceId=q.raceId WHERE x.raceId IS NULL) AS qualifying_orphan_race,
  (SELECT COUNT(*) FROM qualifying q LEFT JOIN drivers x ON x.driverId=q.driverId WHERE x.driverId IS NULL) AS qualifying_orphan_driver,
  (SELECT COUNT(*) FROM qualifying q LEFT JOIN constructors x ON x.constructorId=q.constructorId WHERE x.constructorId IS NULL) AS qualifying_orphan_constructor,
  (SELECT COUNT(*) FROM pit_stops p LEFT JOIN races x ON x.raceId=p.raceId WHERE x.raceId IS NULL) AS pit_orphan_race,
  (SELECT COUNT(*) FROM pit_stops p LEFT JOIN drivers x ON x.driverId=p.driverId WHERE x.driverId IS NULL) AS pit_orphan_driver,
  (SELECT COUNT(*) FROM lap_times l LEFT JOIN races x ON x.raceId=l.raceId WHERE x.raceId IS NULL) AS lap_orphan_race,
  (SELECT COUNT(*) FROM lap_times l LEFT JOIN drivers x ON x.driverId=l.driverId WHERE x.driverId IS NULL) AS lap_orphan_driver,
  (SELECT COUNT(*) FROM results r LEFT JOIN races x ON x.raceId=r.raceId WHERE x.raceId IS NULL) AS results_orphan_race,
  (SELECT COUNT(*) FROM results r LEFT JOIN drivers x ON x.driverId=r.driverId WHERE x.driverId IS NULL) AS results_orphan_driver,
  (SELECT COUNT(*) FROM results r LEFT JOIN constructors x ON x.constructorId=r.constructorId WHERE x.constructorId IS NULL) AS results_orphan_constructor,
  (SELECT COUNT(*) FROM results r LEFT JOIN status x ON x.statusId=r.statusId WHERE x.statusId IS NULL) AS results_orphan_status,
  (SELECT COUNT(*) FROM sprint_results s LEFT JOIN races x ON x.raceId=s.raceId WHERE x.raceId IS NULL) AS sprint_orphan_race,
  (SELECT COUNT(*) FROM sprint_results s LEFT JOIN drivers x ON x.driverId=s.driverId WHERE x.driverId IS NULL) AS sprint_orphan_driver,
  (SELECT COUNT(*) FROM sprint_results s LEFT JOIN constructors x ON x.constructorId=s.constructorId WHERE x.constructorId IS NULL) AS sprint_orphan_constructor,
  (SELECT COUNT(*) FROM sprint_results s LEFT JOIN status x ON x.statusId=s.statusId WHERE x.statusId IS NULL) AS sprint_orphan_status,
  (SELECT COUNT(*) FROM driver_standings d LEFT JOIN races x ON x.raceId=d.raceId WHERE x.raceId IS NULL) AS driver_standings_orphan_race,
  (SELECT COUNT(*) FROM driver_standings d LEFT JOIN drivers x ON x.driverId=d.driverId WHERE x.driverId IS NULL) AS driver_standings_orphan_driver,
  (SELECT COUNT(*) FROM constructor_standings c LEFT JOIN races x ON x.raceId=c.raceId WHERE x.raceId IS NULL) AS constructor_standings_orphan_race,
  (SELECT COUNT(*) FROM constructor_standings c LEFT JOIN constructors x ON x.constructorId=c.constructorId WHERE x.constructorId IS NULL) AS constructor_standings_orphan_constructor,
  (SELECT COUNT(*) FROM constructor_results c LEFT JOIN races x ON x.raceId=c.raceId WHERE x.raceId IS NULL) AS constructor_results_orphan_race,
  (SELECT COUNT(*) FROM constructor_results c LEFT JOIN constructors x ON x.constructorId=c.constructorId WHERE x.constructorId IS NULL) AS constructor_results_orphan_constructor;

-- P4. Range sanity.
SELECT MIN(year) AS first_season, MAX(year) AS last_season, COUNT(*) AS total_races FROM races;
