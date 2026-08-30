-- Slopewatch warehouse — MySQL 8.0
--
-- Design notes
--   * Star schema. Facts are narrow and keyed on (cell_id, date_id); every
--     rolling window, ranking and cohort in the analysis is a window function
--     over these tables rather than pandas code, so the model and the
--     application consume exactly the same definitions.
--   * No geometry columns. Distance work (roads within N km of a cell, nearest
--     stream) is a one-time precompute done in GeoPandas and landed in
--     fact_exposure. Keeping MySQL free of spatial types avoids the SRID 4326
--     axis-order trap and keeps the schema portable.
--   * date_id is YYYYMMDD and cell_id is lat_idx * 1000 + lon_idx. Both are
--     deterministic, so a reload never reshuffles keys.

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 1;


-- ==========================================================================
-- dimensions
-- ==========================================================================

CREATE TABLE IF NOT EXISTS dim_date (
    date_id       INT UNSIGNED     NOT NULL COMMENT 'YYYYMMDD',
    full_date     DATE             NOT NULL,
    year          SMALLINT UNSIGNED NOT NULL,
    quarter       TINYINT UNSIGNED NOT NULL,
    month         TINYINT UNSIGNED NOT NULL,
    month_name    VARCHAR(12)      NOT NULL,
    day           TINYINT UNSIGNED NOT NULL,
    day_of_year   SMALLINT UNSIGNED NOT NULL,
    week_of_year  TINYINT UNSIGNED NOT NULL,
    season        VARCHAR(16)      NOT NULL COMMENT 'winter/pre-monsoon/monsoon/post-monsoon',
    is_monsoon    TINYINT(1)       NOT NULL DEFAULT 0,
    PRIMARY KEY (date_id),
    UNIQUE KEY uq_dim_date_full (full_date),
    KEY ix_dim_date_year_month (year, month),
    KEY ix_dim_date_doy (day_of_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS dim_cell (
    cell_id          INT UNSIGNED     NOT NULL COMMENT 'lat_idx * 1000 + lon_idx',
    lat_idx          SMALLINT UNSIGNED NOT NULL,
    lon_idx          SMALLINT UNSIGNED NOT NULL,
    lat_c            DECIMAL(6,3)     NOT NULL COMMENT 'cell centroid latitude',
    lon_c            DECIMAL(7,3)     NOT NULL COMMENT 'cell centroid longitude',

    state_name       VARCHAR(64)      NULL,
    district_name    VARCHAR(64)      NULL,

    -- terrain, populated in the DEM phase
    elev_mean        DECIMAL(7,1)     NULL,
    elev_min         DECIMAL(7,1)     NULL,
    elev_max         DECIMAL(7,1)     NULL,
    elev_range       DECIMAL(7,1)     NULL,
    slope_mean       DECIMAL(5,2)     NULL,
    slope_max        DECIMAL(5,2)     NULL,
    slope_std        DECIMAL(5,2)     NULL,
    aspect_sin       DECIMAL(6,4)     NULL,
    aspect_cos       DECIMAL(6,4)     NULL,
    tri              DECIMAL(7,2)     NULL COMMENT 'terrain ruggedness index',
    twi              DECIMAL(6,2)     NULL COMMENT 'topographic wetness index',

    -- context, populated in the OSM phase
    landcover_class  VARCHAR(32)      NULL,
    dist_road_km     DECIMAL(7,2)     NULL,
    dist_stream_km   DECIMAL(7,2)     NULL,
    road_density     DECIMAL(7,3)     NULL COMMENT 'km of road per sq km',

    is_hill          TINYINT(1)       NOT NULL DEFAULT 0 COMMENT 'passed the slope mask',
    in_study_area    TINYINT(1)       NOT NULL DEFAULT 1,
    created_at       TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (cell_id),
    UNIQUE KEY uq_dim_cell_idx (lat_idx, lon_idx),
    KEY ix_dim_cell_latlon (lat_c, lon_c),
    KEY ix_dim_cell_state (state_name),
    KEY ix_dim_cell_hill (is_hill)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- Road and settlement dimensions are loaded from the Geofabrik extracts and
-- exist so the exposure precompute can be traced back to named features.
CREATE TABLE IF NOT EXISTS dim_road (
    road_id      BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    osm_id       VARCHAR(32)     NOT NULL,
    road_name    VARCHAR(255)    NULL,
    road_class   VARCHAR(32)     NULL COMMENT 'motorway/trunk/primary/...',
    length_km    DECIMAL(9,3)    NULL,
    PRIMARY KEY (road_id),
    UNIQUE KEY uq_dim_road_osm (osm_id),
    KEY ix_dim_road_class (road_class)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS dim_settlement (
    settlement_id  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    osm_id         VARCHAR(32)     NOT NULL,
    place_name     VARCHAR(255)    NULL,
    place_type     VARCHAR(32)     NULL COMMENT 'city/town/village/hamlet',
    latitude       DECIMAL(9,6)    NOT NULL,
    longitude      DECIMAL(9,6)    NOT NULL,
    cell_id        INT UNSIGNED    NULL,
    est_population INT UNSIGNED    NULL,
    PRIMARY KEY (settlement_id),
    UNIQUE KEY uq_dim_settlement_osm (osm_id),
    KEY ix_dim_settlement_cell (cell_id),
    KEY ix_dim_settlement_type (place_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ==========================================================================
-- facts
-- ==========================================================================

CREATE TABLE IF NOT EXISTS fact_landslide (
    event_id          BIGINT UNSIGNED NOT NULL COMMENT 'NASA GLC event_id',
    cell_id           INT UNSIGNED    NOT NULL,
    date_id           INT UNSIGNED    NOT NULL,
    event_date        DATE            NOT NULL,
    latitude          DECIMAL(9,6)    NOT NULL,
    longitude         DECIMAL(9,6)    NOT NULL,
    country_name      VARCHAR(64)     NULL COMMENT 'bbox spans the whole Himalayan arc, not only India',
    state_name        VARCHAR(64)     NULL COMMENT 'as reported; spelling is inconsistent upstream',
    state_name_norm   VARCHAR(64)     NULL COMMENT 'diacritics folded, use this for grouping',
    landslide_category VARCHAR(32)    NULL,
    landslide_trigger VARCHAR(32)     NULL,
    landslide_size    VARCHAR(16)     NULL,
    location_accuracy VARCHAR(16)     NULL,
    loc_accuracy_km   DECIMAL(6,2)    NULL COMMENT 'parsed from location_accuracy',
    fatality_count    INT UNSIGNED    NULL,
    injury_count      INT UNSIGNED    NULL,
    source_name       VARCHAR(128)    NULL,
    event_title       VARCHAR(512)    NULL,

    PRIMARY KEY (event_id),
    KEY ix_fl_cell_date (cell_id, date_id),
    KEY ix_fl_date (date_id),
    KEY ix_fl_state (state_name_norm),
    KEY ix_fl_country (country_name),
    KEY ix_fl_trigger (landslide_trigger),
    CONSTRAINT fk_fl_cell FOREIGN KEY (cell_id) REFERENCES dim_cell (cell_id),
    CONSTRAINT fk_fl_date FOREIGN KEY (date_id) REFERENCES dim_date (date_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- The large table. No foreign keys here: it is bulk loaded in six-figure
-- batches and the constraint check costs more than it protects, since both
-- parents are populated first by the same pipeline.
CREATE TABLE IF NOT EXISTS fact_weather_daily (
    cell_id          INT UNSIGNED  NOT NULL,
    date_id          INT UNSIGNED  NOT NULL,
    precip_mm        DECIMAL(7,2)  NULL,
    rain_mm          DECIMAL(7,2)  NULL,
    temp_max         DECIMAL(5,2)  NULL,
    temp_min         DECIMAL(5,2)  NULL,
    temp_mean        DECIMAL(5,2)  NULL,
    sm_0_7           DECIMAL(6,4)  NULL COMMENT 'volumetric soil moisture m3/m3',
    sm_7_28          DECIMAL(6,4)  NULL,
    sm_28_100        DECIMAL(6,4)  NULL,
    et0_mm           DECIMAL(6,2)  NULL,
    wind_max         DECIMAL(6,2)  NULL,
    river_discharge  DECIMAL(12,3) NULL COMMENT 'GloFAS m3/s',

    PRIMARY KEY (cell_id, date_id),
    KEY ix_fw_date (date_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- The case-control modelling frame. One row per (cell, date) drawn into the
-- sample, with the stratum recorded so the composition can be audited and
-- defended rather than being an unexplained artefact of a random seed.
CREATE TABLE IF NOT EXISTS fact_sample (
    sample_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    cell_id     INT UNSIGNED    NOT NULL,
    date_id     INT UNSIGNED    NOT NULL,
    label       TINYINT(1)      NOT NULL COMMENT '1 = landslide occurred',
    stratum     VARCHAR(24)     NOT NULL COMMENT 'case/temporal/spatial/background',
    event_id    BIGINT UNSIGNED NULL COMMENT 'set for cases only',
    split       VARCHAR(12)     NULL COMMENT 'train/val/test, assigned by date',
    region_block VARCHAR(32)    NULL COMMENT 'for leave-one-region-out CV',

    PRIMARY KEY (sample_id),
    UNIQUE KEY uq_fs_cell_date (cell_id, date_id),
    KEY ix_fs_label (label),
    KEY ix_fs_split (split),
    KEY ix_fs_stratum (stratum)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS fact_exposure (
    cell_id            INT UNSIGNED NOT NULL,
    road_km_total      DECIMAL(9,3) NULL,
    road_km_primary    DECIMAL(9,3) NULL,
    bridges            INT UNSIGNED NULL,
    settlements        INT UNSIGNED NULL,
    schools            INT UNSIGNED NULL,
    health_facilities  INT UNSIGNED NULL,
    est_population     INT UNSIGNED NULL,
    exposure_score     DECIMAL(7,3) NULL COMMENT 'normalised 0-1, used in prioritisation',
    computed_at        TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (cell_id),
    CONSTRAINT fk_fe_cell FOREIGN KEY (cell_id) REFERENCES dim_cell (cell_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS fact_risk_pred (
    cell_id        INT UNSIGNED  NOT NULL,
    date_id        INT UNSIGNED  NOT NULL,
    model_version  VARCHAR(32)   NOT NULL,
    probability    DECIMAL(6,5)  NOT NULL COMMENT 'relative risk, calibrated on the case-control sample',
    absolute_probability DECIMAL(12,10) NULL COMMENT 'prior-corrected population frequency',
    risk_band      VARCHAR(16)   NOT NULL COMMENT 'low/moderate/elevated/high/critical',
    priority_score DECIMAL(7,3)  NULL COMMENT 'probability weighted by exposure',
    driver_1       VARCHAR(48)   NULL COMMENT 'top SHAP contributor',
    driver_2       VARCHAR(48)   NULL,
    driver_3       VARCHAR(48)   NULL,
    scored_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (cell_id, date_id, model_version),
    KEY ix_frp_date_band (date_id, risk_band),
    KEY ix_frp_priority (date_id, priority_score)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ==========================================================================
-- operations
-- ==========================================================================

CREATE TABLE IF NOT EXISTS etl_run_log (
    run_id       BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    step_name    VARCHAR(64)     NOT NULL,
    status       VARCHAR(16)     NOT NULL COMMENT 'started/succeeded/failed',
    rows_written BIGINT UNSIGNED NULL,
    message      TEXT            NULL,
    started_at   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at  TIMESTAMP       NULL,
    PRIMARY KEY (run_id),
    KEY ix_etl_step (step_name, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
