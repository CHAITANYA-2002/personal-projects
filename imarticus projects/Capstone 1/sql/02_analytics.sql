-- Slopewatch analytical layer — MySQL 8.0
--
-- These views are not a syllabus exercise bolted onto a Python project. The
-- rolling windows defined here are the same aggregates the dashboard reports,
-- so a rainfall accumulation is defined exactly once and both the analysis and
-- Power BI read the same definition. Where a calculation exists in both places
-- it eventually disagrees in one of them.
--
-- Run after the warehouse is loaded:
--   SOURCE sql/02_analytics.sql

-- ==========================================================================
-- v_weather_rolling
-- Rolling rainfall and soil-moisture dynamics per cell-day.
-- Window functions over (cell_id ORDER BY date_id) — the frame is what makes
-- this a single pass instead of a self-join per window.
-- ==========================================================================
-- Two passes, not one. MySQL refuses to rank over a window function directly
-- ("cannot nest a window function in the specification of window"), so the
-- accumulations are computed in a derived table and the percentile is taken
-- over the materialised column.
CREATE OR REPLACE VIEW v_weather_rolling AS
SELECT
    r.*,
    -- Percentile of this day's 7-day accumulation against everything else the
    -- same cell has seen. 200 mm in Cherrapunji and 200 mm in Leh are not the
    -- same event, and an absolute threshold cannot tell them apart.
    PERCENT_RANK() OVER (
        PARTITION BY r.cell_id ORDER BY r.rain_7d
    )                                 AS rain_7d_percentile
FROM (
    SELECT
        w.cell_id,
        w.date_id,
        w.precip_mm,
        w.sm_0_7,
        w.sm_7_28,
        w.river_discharge,

        SUM(w.precip_mm) OVER win_3   AS rain_3d,
        SUM(w.precip_mm) OVER win_7   AS rain_7d,
        SUM(w.precip_mm) OVER win_15  AS rain_15d,
        SUM(w.precip_mm) OVER win_30  AS rain_30d,
        MAX(w.precip_mm) OVER win_7   AS rain_max_1d_in_7,

        -- How fast the profile is wetting matters more than how wet it is.
        w.sm_0_7 - LAG(w.sm_0_7, 1) OVER cell_time  AS sm_delta_1d,
        w.sm_0_7 - LAG(w.sm_0_7, 7) OVER cell_time  AS sm_delta_7d
    FROM fact_weather_daily w
    WINDOW
        cell_time AS (PARTITION BY w.cell_id ORDER BY w.date_id),
        win_3  AS (PARTITION BY w.cell_id ORDER BY w.date_id ROWS BETWEEN 2  PRECEDING AND CURRENT ROW),
        win_7  AS (PARTITION BY w.cell_id ORDER BY w.date_id ROWS BETWEEN 6  PRECEDING AND CURRENT ROW),
        win_15 AS (PARTITION BY w.cell_id ORDER BY w.date_id ROWS BETWEEN 14 PRECEDING AND CURRENT ROW),
        win_30 AS (PARTITION BY w.cell_id ORDER BY w.date_id ROWS BETWEEN 29 PRECEDING AND CURRENT ROW)
) r;


-- ==========================================================================
-- v_event_detail
-- Every recorded landslide with its calendar and terrain context attached.
-- The join every historical question starts from.
-- ==========================================================================
CREATE OR REPLACE VIEW v_event_detail AS
SELECT
    f.event_id,
    f.cell_id,
    f.date_id,
    f.event_date,
    f.country_name,
    f.state_name_norm                                   AS state,
    f.landslide_trigger,
    f.landslide_size,
    f.fatality_count,
    f.injury_count,
    f.loc_accuracy_km,
    d.year,
    d.month,
    d.month_name,
    d.season,
    d.is_monsoon,
    c.lat_c,
    c.lon_c,
    c.elev_mean,
    c.slope_mean,
    c.slope_max,
    c.tri,
    c.is_hill,
    CASE
        WHEN c.elev_mean <  500 THEN '0-500m'
        WHEN c.elev_mean < 1000 THEN '500-1000m'
        WHEN c.elev_mean < 2000 THEN '1000-2000m'
        WHEN c.elev_mean < 3000 THEN '2000-3000m'
        ELSE '3000m+'
    END                                                 AS elevation_band
FROM        fact_landslide f
JOIN        dim_date       d ON d.date_id = f.date_id
JOIN        dim_cell       c ON c.cell_id = f.cell_id;


-- ==========================================================================
-- v_cell_neighbours
-- The 3x3 block around each cell, derived from the grid index rather than
-- from geometry. Used for spatially smoothed rainfall and neighbour history.
-- ==========================================================================
CREATE OR REPLACE VIEW v_cell_neighbours AS
SELECT
    c.cell_id                                           AS cell_id,
    n.cell_id                                           AS neighbour_id,
    ABS(CAST(c.lat_idx AS SIGNED) - CAST(n.lat_idx AS SIGNED))
      + ABS(CAST(c.lon_idx AS SIGNED) - CAST(n.lon_idx AS SIGNED))
                                                        AS grid_distance
FROM        dim_cell c
JOIN        dim_cell n
       ON   ABS(CAST(c.lat_idx AS SIGNED) - CAST(n.lat_idx AS SIGNED)) <= 1
      AND   ABS(CAST(c.lon_idx AS SIGNED) - CAST(n.lon_idx AS SIGNED)) <= 1
      AND   c.cell_id <> n.cell_id
WHERE       c.is_hill = 1;


-- ==========================================================================
-- v_intensity_duration
-- The classic landslide-hydrology diagnostic: mean rainfall intensity against
-- the duration of the storm that produced it, on log-log axes. Events should
-- sit above the threshold curve; if ours do not, the weather data is not
-- resolving the trigger and no amount of model tuning will fix that.
-- ==========================================================================
CREATE OR REPLACE VIEW v_intensity_duration AS
SELECT
    e.event_id,
    e.state,
    e.event_date,
    r.rain_3d,
    r.rain_7d,
    r.rain_15d,
    ROUND(r.rain_3d  / 3,  2)                           AS intensity_3d_mm_per_day,
    ROUND(r.rain_7d  / 7,  2)                           AS intensity_7d_mm_per_day,
    ROUND(r.rain_15d / 15, 2)                           AS intensity_15d_mm_per_day,
    r.rain_7d_percentile
FROM        v_event_detail   e
JOIN        v_weather_rolling r
       ON   r.cell_id = e.cell_id AND r.date_id = e.date_id;


-- ==========================================================================
-- mart_event_history
-- Historical page. Events by state, year, month and elevation band, with the
-- year-over-year movement Power BI would otherwise recompute in DAX.
-- ==========================================================================
DROP TABLE IF EXISTS mart_event_history;
CREATE TABLE mart_event_history AS
WITH yearly AS (
    SELECT
        state,
        elevation_band,
        year,
        month,
        month_name,
        season,
        COUNT(*)                                        AS events,
        SUM(fatality_count)                             AS deaths,
        SUM(is_monsoon)                                 AS monsoon_events,
        ROUND(AVG(slope_mean), 2)                       AS mean_slope,
        ROUND(AVG(elev_mean), 0)                        AS mean_elevation
    FROM        v_event_detail
    WHERE       country_name = 'India'
    GROUP BY    state, elevation_band, year, month, month_name, season
)
SELECT
    y.*,
    SUM(y.events) OVER (
        PARTITION BY y.state ORDER BY y.year, y.month
    )                                                   AS cumulative_events,
    LAG(y.events, 12) OVER (
        PARTITION BY y.state ORDER BY y.year, y.month
    )                                                   AS events_same_month_last_year
FROM yearly y;

CREATE INDEX ix_meh_state_year ON mart_event_history (state, year);


-- ==========================================================================
-- mart_cell_daily_risk
-- What the map reads. One row per cell-day the model has scored, joined to the
-- terrain and exposure that turn a probability into an instruction.
-- ==========================================================================
CREATE OR REPLACE VIEW mart_cell_daily_risk AS
SELECT
    p.cell_id,
    p.date_id,
    d.full_date,
    p.model_version,
    p.probability,
    p.absolute_probability,
    p.risk_band,
    p.priority_score,
    p.driver_1,
    p.driver_2,
    p.driver_3,
    c.lat_c,
    c.lon_c,
    c.state_name,
    c.district_name,
    c.elev_mean,
    c.slope_mean,
    COALESCE(x.road_km_total, 0)                        AS road_km_total,
    COALESCE(x.settlements, 0)                          AS settlements,
    COALESCE(x.est_population, 0)                       AS est_population,
    COALESCE(x.schools, 0)                              AS schools,
    COALESCE(x.health_facilities, 0)                    AS health_facilities
FROM        fact_risk_pred p
JOIN        dim_date       d ON d.date_id = p.date_id
JOIN        dim_cell       c ON c.cell_id = p.cell_id
LEFT JOIN   fact_exposure  x ON x.cell_id = p.cell_id;


-- ==========================================================================
-- mart_district_daily_risk
-- The command-centre table. Districts ranked by the exposure-weighted risk of
-- their worst cells, not by an average that hides a single critical cell in a
-- quiet district.
-- ==========================================================================
CREATE OR REPLACE VIEW mart_district_daily_risk AS
SELECT
    r.date_id,
    r.full_date,
    r.state_name,
    r.district_name,
    COUNT(*)                                            AS cells_scored,
    SUM(r.risk_band = 'critical')                       AS critical_cells,
    SUM(r.risk_band = 'high')                           AS high_cells,
    ROUND(MAX(r.probability), 4)                        AS max_probability,
    ROUND(AVG(r.probability), 4)                        AS mean_probability,
    ROUND(SUM(r.priority_score), 3)                     AS total_priority,
    SUM(r.road_km_total)                                AS road_km_at_risk,
    SUM(r.settlements)                                  AS settlements_at_risk,
    SUM(r.est_population)                               AS population_at_risk,
    DENSE_RANK() OVER (
        PARTITION BY r.date_id ORDER BY SUM(r.priority_score) DESC
    )                                                   AS priority_rank
FROM        mart_cell_daily_risk r
-- 'moderate' is included deliberately: on a quiet day it is the top of the
-- distribution, and a district table that empties itself outside the monsoon
-- reads as a broken report rather than as low risk.
WHERE       r.risk_band IN ('moderate', 'elevated', 'high', 'critical')
GROUP BY    r.date_id, r.full_date, r.state_name, r.district_name;


-- ==========================================================================
-- v_alert_precision
-- Production truth. Joins yesterday's alerts to what actually happened, so the
-- dashboard reports how the system performs in the field rather than only how
-- it scored in a notebook.
-- ==========================================================================
CREATE OR REPLACE VIEW v_alert_precision AS
SELECT
    p.date_id,
    p.model_version,
    p.risk_band,
    COUNT(*)                                            AS alerts,
    SUM(f.event_id IS NOT NULL)                         AS alerts_with_event,
    ROUND(
        100.0 * SUM(f.event_id IS NOT NULL) / NULLIF(COUNT(*), 0), 2
    )                                                   AS precision_pct
FROM        fact_risk_pred p
LEFT JOIN   fact_landslide f
       ON   f.cell_id = p.cell_id
      AND   f.date_id BETWEEN p.date_id AND p.date_id + 3
WHERE       p.risk_band IN ('high', 'critical')
GROUP BY    p.date_id, p.model_version, p.risk_band;


-- ==========================================================================
-- v_settlements_at_risk
-- The exposure count turned back into places. "Forty settlements" ranks a cell;
-- it does not tell a team where to go. This names them, largest first, so the
-- cell detail view and any dispatch list can be acted on directly.
-- ==========================================================================
CREATE OR REPLACE VIEW v_settlements_at_risk AS
SELECT
    p.cell_id,
    p.date_id,
    p.risk_band,
    p.probability,
    s.place_name,
    s.place_type,
    s.est_population,
    s.latitude,
    s.longitude,
    c.district_name,
    c.state_name,
    ROW_NUMBER() OVER (
        PARTITION BY p.cell_id, p.date_id
        ORDER BY s.est_population DESC
    )                                                   AS size_rank
FROM        fact_risk_pred  p
JOIN        dim_settlement  s ON s.cell_id = p.cell_id
JOIN        dim_cell        c ON c.cell_id = p.cell_id
WHERE       p.risk_band <> 'low';
