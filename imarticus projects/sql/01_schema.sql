-- ============================================================
-- 01 — SCHEMA  (MySQL 8.0)
-- F1 World Championship 1950–2024 — 14 tables, PK/FK constraints.
--   mysql -u root -proot < 01_schema.sql
-- ============================================================

DROP DATABASE IF EXISTS f1_analytics;
CREATE DATABASE f1_analytics CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE f1_analytics;

-- ---------- Dimension tables ----------

CREATE TABLE circuits (
    circuitId   INT PRIMARY KEY,
    circuitRef  VARCHAR(255),
    name        VARCHAR(255),
    location    VARCHAR(255),
    country     VARCHAR(255),
    lat         DOUBLE,
    lng         DOUBLE,
    alt         INT,
    url         VARCHAR(500)
) ENGINE=InnoDB;

CREATE TABLE constructors (
    constructorId   INT PRIMARY KEY,
    constructorRef  VARCHAR(255),
    name            VARCHAR(255),
    nationality     VARCHAR(255),
    url             VARCHAR(500)
) ENGINE=InnoDB;

CREATE TABLE drivers (
    driverId    INT PRIMARY KEY,
    driverRef   VARCHAR(255),
    number      INT,
    code        VARCHAR(10),
    forename    VARCHAR(255),
    surname     VARCHAR(255),
    dob         DATE,
    nationality VARCHAR(255),
    url         VARCHAR(500)
) ENGINE=InnoDB;

CREATE TABLE seasons (
    year  INT PRIMARY KEY,
    url   VARCHAR(500)
) ENGINE=InnoDB;

CREATE TABLE status (
    statusId  INT PRIMARY KEY,
    status    VARCHAR(255)
) ENGINE=InnoDB;

CREATE TABLE races (
    raceId      INT PRIMARY KEY,
    year        INT,
    round       INT,
    circuitId   INT,
    name        VARCHAR(255),
    date        DATE,
    time        VARCHAR(20),
    url         VARCHAR(500),
    fp1_date    DATE, fp1_time VARCHAR(20),
    fp2_date    DATE, fp2_time VARCHAR(20),
    fp3_date    DATE, fp3_time VARCHAR(20),
    quali_date  DATE, quali_time VARCHAR(20),
    sprint_date DATE, sprint_time VARCHAR(20),
    CONSTRAINT fk_races_season  FOREIGN KEY (year)      REFERENCES seasons(year),
    CONSTRAINT fk_races_circuit FOREIGN KEY (circuitId) REFERENCES circuits(circuitId)
) ENGINE=InnoDB;

-- ---------- Fact / event tables ----------

CREATE TABLE qualifying (
    qualifyId      INT PRIMARY KEY,
    raceId         INT,
    driverId       INT,
    constructorId  INT,
    number         INT,
    position       INT,
    q1             VARCHAR(20),
    q2             VARCHAR(20),
    q3             VARCHAR(20),
    CONSTRAINT fk_qual_race        FOREIGN KEY (raceId)        REFERENCES races(raceId),
    CONSTRAINT fk_qual_driver      FOREIGN KEY (driverId)      REFERENCES drivers(driverId),
    CONSTRAINT fk_qual_constructor FOREIGN KEY (constructorId) REFERENCES constructors(constructorId)
) ENGINE=InnoDB;

CREATE TABLE pit_stops (
    raceId        INT,
    driverId      INT,
    stop          INT,
    lap           INT,
    time          VARCHAR(20),
    duration      VARCHAR(20),
    milliseconds  INT,
    PRIMARY KEY (raceId, driverId, stop),
    CONSTRAINT fk_pit_race   FOREIGN KEY (raceId)   REFERENCES races(raceId),
    CONSTRAINT fk_pit_driver FOREIGN KEY (driverId) REFERENCES drivers(driverId)
) ENGINE=InnoDB;

CREATE TABLE lap_times (
    raceId        INT,
    driverId      INT,
    lap           INT,
    position      INT,
    time          VARCHAR(20),
    milliseconds  INT,
    PRIMARY KEY (raceId, driverId, lap),
    CONSTRAINT fk_lap_race   FOREIGN KEY (raceId)   REFERENCES races(raceId),
    CONSTRAINT fk_lap_driver FOREIGN KEY (driverId) REFERENCES drivers(driverId)
) ENGINE=InnoDB;

CREATE TABLE results (
    resultId         INT PRIMARY KEY,
    raceId           INT,
    driverId         INT,
    constructorId    INT,
    number           INT,
    grid             INT,
    position         INT,            -- NULL for DNF; use positionOrder to sort
    positionText     VARCHAR(10),
    positionOrder    INT,
    points           FLOAT,
    laps             INT,
    time             VARCHAR(30),
    milliseconds     INT,            -- winner = total race time; others = gap
    fastestLap       INT,
    `rank`           INT,
    fastestLapTime   VARCHAR(30),
    fastestLapSpeed  VARCHAR(30),
    statusId         INT,
    CONSTRAINT fk_res_race        FOREIGN KEY (raceId)        REFERENCES races(raceId),
    CONSTRAINT fk_res_driver      FOREIGN KEY (driverId)      REFERENCES drivers(driverId),
    CONSTRAINT fk_res_constructor FOREIGN KEY (constructorId) REFERENCES constructors(constructorId),
    CONSTRAINT fk_res_status      FOREIGN KEY (statusId)      REFERENCES status(statusId)
) ENGINE=InnoDB;

CREATE TABLE sprint_results (
    resultId         INT PRIMARY KEY,
    raceId           INT,
    driverId         INT,
    constructorId    INT,
    number           INT,
    grid             INT,
    position         INT,
    positionText     VARCHAR(10),
    positionOrder    INT,
    points           FLOAT,
    laps             INT,
    time             VARCHAR(30),
    milliseconds     INT,
    fastestLap       INT,
    fastestLapTime   VARCHAR(30),
    statusId         INT,
    CONSTRAINT fk_sprint_race        FOREIGN KEY (raceId)        REFERENCES races(raceId),
    CONSTRAINT fk_sprint_driver      FOREIGN KEY (driverId)      REFERENCES drivers(driverId),
    CONSTRAINT fk_sprint_constructor FOREIGN KEY (constructorId) REFERENCES constructors(constructorId),
    CONSTRAINT fk_sprint_status      FOREIGN KEY (statusId)      REFERENCES status(statusId)
) ENGINE=InnoDB;

CREATE TABLE driver_standings (
    driverStandingsId  INT PRIMARY KEY,
    raceId             INT,
    driverId           INT,
    points             FLOAT,          -- cumulative up to this race
    position           INT,
    positionText       VARCHAR(10),
    wins               INT,
    CONSTRAINT fk_ds_race   FOREIGN KEY (raceId)   REFERENCES races(raceId),
    CONSTRAINT fk_ds_driver FOREIGN KEY (driverId) REFERENCES drivers(driverId)
) ENGINE=InnoDB;

CREATE TABLE constructor_standings (
    constructorStandingsId  INT PRIMARY KEY,
    raceId                  INT,
    constructorId           INT,
    points                  FLOAT,
    position                INT,
    positionText            VARCHAR(10),
    wins                    INT,
    CONSTRAINT fk_cs_race        FOREIGN KEY (raceId)        REFERENCES races(raceId),
    CONSTRAINT fk_cs_constructor FOREIGN KEY (constructorId) REFERENCES constructors(constructorId)
) ENGINE=InnoDB;

CREATE TABLE constructor_results (
    constructorResultsId  INT PRIMARY KEY,
    raceId                INT,
    constructorId         INT,
    points                FLOAT,
    status                VARCHAR(10),
    CONSTRAINT fk_cr_race        FOREIGN KEY (raceId)        REFERENCES races(raceId),
    CONSTRAINT fk_cr_constructor FOREIGN KEY (constructorId) REFERENCES constructors(constructorId)
) ENGINE=InnoDB;
