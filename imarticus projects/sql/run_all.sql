-- ============================================================
-- MASTER RUNNER — rebuilds the DB and runs every script in order.
-- Run from THIS folder (the sql/ directory) inside the mysql client:
--   mysql --local-infile=1 -u root -proot
--   mysql> SET GLOBAL local_infile = 1;
--   mysql> SOURCE run_all.sql;
-- (SOURCE paths are resolved relative to your current working directory.)
-- ============================================================

SOURCE 01_schema.sql;
SOURCE 02_load_data.sql;
SOURCE 03_data_profiling.sql;
SOURCE 04_tier1_foundation.sql;
SOURCE 05_tier2_intermediate.sql;
SOURCE 06_tier3_advanced.sql;
SOURCE 07_tier4_expert.sql;
SOURCE 08_views.sql;
SOURCE 09_procedures.sql;
SOURCE 10_capstone_thesis.sql;
