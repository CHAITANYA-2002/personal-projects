-- ============================================================
-- TIER 1 — FOUNDATION
-- SELECT / WHERE / ORDER BY / GROUP BY / HAVING / aggregates / COUNT(DISTINCT)
-- ============================================================
USE f1_analytics;

-- Q1. All circuits located in Italy.
SELECT name, location, country
FROM circuits
WHERE country = 'Italy'
ORDER BY name;

-- Q2. Races held in each decade.
SELECT FLOOR(year/10)*10 AS decade, COUNT(*) AS races
FROM races
GROUP BY decade
ORDER BY decade;

-- Q3. Nationalities that produced the most drivers (top 10).
SELECT nationality, COUNT(*) AS driver_count
FROM drivers
GROUP BY nationality
ORDER BY driver_count DESC
LIMIT 10;

-- Q4. Constructors that have entered more than 100 races.
SELECT c.name, COUNT(DISTINCT r.raceId) AS race_entries
FROM constructors c
JOIN results r ON r.constructorId = c.constructorId
GROUP BY c.constructorId, c.name
HAVING race_entries > 100
ORDER BY race_entries DESC;

-- Q5. Average races per season, and the seasons above that average.
SELECT ROUND(AVG(cnt),2) AS avg_races_per_season
FROM (SELECT year, COUNT(*) cnt FROM races GROUP BY year) t;

SELECT year, COUNT(*) AS races
FROM races
GROUP BY year
HAVING races > (SELECT AVG(cnt) FROM (SELECT COUNT(*) cnt FROM races GROUP BY year) t)
ORDER BY races DESC, year;

-- Q6. Drivers born after 1990.
SELECT forename, surname, dob, nationality
FROM drivers
WHERE dob > '1990-12-31'
ORDER BY dob;

-- Q7. Distinct countries visited, and the countries hosting the most circuits.
SELECT COUNT(DISTINCT country) AS distinct_countries FROM circuits;

SELECT country, COUNT(*) AS circuits
FROM circuits
GROUP BY country
ORDER BY circuits DESC
LIMIT 5;

-- Q8. Most common finish-status reasons across all results.
SELECT s.status, COUNT(*) AS occurrences
FROM results r
JOIN status s ON s.statusId = r.statusId
GROUP BY s.status
ORDER BY occurrences DESC
LIMIT 10;
