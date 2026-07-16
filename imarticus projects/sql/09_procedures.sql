-- ============================================================
-- 09 — STORED PROCEDURE  (sp_season_report)
-- ============================================================
USE f1_analytics;

DROP PROCEDURE IF EXISTS sp_season_report;

DELIMITER $$
CREATE PROCEDURE sp_season_report(IN p_year INT)
BEGIN
  -- The season's final drivers' standings (top 10).
  SELECT CONCAT(d.forename,' ',d.surname) AS driver, ds.points, ds.wins, ds.position
  FROM driver_standings ds
  JOIN races ra   ON ra.raceId = ds.raceId
  JOIN drivers d  ON d.driverId = ds.driverId
  WHERE ra.year = p_year
    AND ra.round = (SELECT MAX(round) FROM races WHERE year = p_year)
  ORDER BY ds.position
  LIMIT 10;

  -- Headline season stats.
  SELECT
    (SELECT COUNT(*) FROM races WHERE year = p_year) AS races_held,
    (SELECT CONCAT(d.forename,' ',d.surname)
       FROM driver_standings ds
       JOIN races ra ON ra.raceId = ds.raceId
       JOIN drivers d ON d.driverId = ds.driverId
      WHERE ra.year = p_year
        AND ra.round = (SELECT MAX(round) FROM races WHERE year = p_year)
        AND ds.position = 1)                          AS champion,
    (SELECT c.name
       FROM constructor_standings cs
       JOIN races ra ON ra.raceId = cs.raceId
       JOIN constructors c ON c.constructorId = cs.constructorId
      WHERE ra.year = p_year
        AND ra.round = (SELECT MAX(round) FROM races WHERE year = p_year)
        AND cs.position = 1)                          AS constructor_champion;
END$$
DELIMITER ;

-- Demo call.
CALL sp_season_report(2021);
