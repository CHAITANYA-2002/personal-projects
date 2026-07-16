# Formula 1 SQL Case Study — What Makes a Champion?

This project turns 75 seasons of Formula 1 data into a reproducible MySQL analytics case study. It is designed to demonstrate both SQL fluency and analytical thinking: the work starts with schema design and data-quality checks, progresses through increasingly advanced queries, and ends with a capstone investigation of the traits associated with a Drivers' World Champion.

The central question is:

> **What separates a World Champion from the rest of the grid: peak speed, consistency, reliability, the car, or some combination of all four?**

The project is not intended to claim that SQL can prove causation. Its purpose is to show how a structured racing database can be queried to create evidence, how that evidence can be interpreted responsibly, and how the analysis can be reproduced by another reviewer.

## Executive summary

The database contains 14 related tables and a snapshot of Formula 1 results from **1950 through 2024**. The analysis compares champions with runners-up, follows drivers across seasons and constructors, studies qualifying and race outcomes, and classifies mechanical failures using the source status table.

The recorded capstone run supports this concise conclusion:

> A champion combines repeatable race-winning pace with the ability to finish. The data shows a strong consistency and reliability advantage, while qualifying position and machinery remain important but incomplete explanations.

The main evidence is:

| Analytical question | Recorded result | Interpretation |
|---|---:|---|
| Do champions win more? | 6.37 average wins per season vs 3.45 for runners-up | Champions have a higher peak-performance output across the title season. |
| Do champions finish more often? | 2.24 DNFs per season vs 3.07 for runners-up | The title advantage is not only speed; it also includes fewer lost races. |
| Is qualifying important? | Pearson `r = -0.4222` across 3,040 driver-seasons | Better average grid position is moderately associated with more points, but it is not deterministic. |
| Does the driver win in different cars? | 26 of 34 race-winning champions, or 76.5%, won for more than one constructor | Champion-level drivers are often successful across more than one team, although the car still matters. |
| Is reliability a major differentiator? | Mechanical-DNF rate: 9.58% for champions vs 23.51% for the rest of the field | Champions experience mechanical failure at less than half the field's recorded rate. |

The values above come from the saved outputs in `results/`. Re-running the SQL scripts against a changed dataset may legitimately change them.

## What this project is meant to display

For a reviewer, the project demonstrates an end-to-end analytics workflow:

1. **Model a relational dataset.** The schema separates dimensions such as drivers, constructors, circuits, seasons, and status codes from event and fact tables such as results, qualifying, lap times, pit stops, and standings.
2. **Load and verify the data.** The load script imports the 14 CSV files, and the profiling script checks row counts, expected NULLs, date ranges, and representative foreign-key relationships.
3. **Build SQL capability progressively.** The tiered scripts move from filtering and aggregation to joins, subqueries, CTEs, window functions, recursive traversal, views, stored procedures, and query-plan inspection.
4. **Answer a coherent business-style question.** The capstone queries are not a random collection of syntax exercises; each one tests a different explanation for championship success.
5. **Communicate findings visually.** The notebook re-runs the headline queries through SQLAlchemy and uses Python only for presentation and charting.

## Dataset and grain

The repository includes the CSV snapshot used by the project. It covers:

- 75 seasons: 1950–2024
- 1,125 races
- 861 drivers
- 212 constructors
- 77 circuits
- 26,759 race-result rows
- 589,081 lap-time rows
- 34,863 driver-standing snapshots

The dataset follows the familiar Ergast/Kaggle-style Formula 1 schema. The existing project materials identify the source as the Kaggle Formula 1 World Championship dataset associated with `rohanrao`. If this work is redistributed outside the repository, confirm the source's current license and attribution requirements first.

The most important grains are:

| Table | Grain | Why it matters |
|---|---|---|
| `races` | One row per race | Supplies season, round, date, circuit, and race identity. |
| `results` | One row per driver in a race | Main fact table for starts, grid, finish, points, laps, and status. |
| `driver_standings` | One row per driver after a race | Used to identify the champion and runner-up at each season's final round. |
| `constructor_standings` | One row per constructor after a race | Used to study constructor championships and team dynasties. |
| `qualifying` | One row per qualifying entry | Stores qualifying-session results; the project uses `results.grid` for an all-era starting-position measure. |
| `lap_times` | One row per driver-lap | Supports lap-level comparisons and window-function demonstrations. |
| `pit_stops` | One row per pit stop | Makes pit-stop analysis possible, even though it is not central to the capstone thesis. |

## Relational model

The schema has 14 tables and 23 foreign-key relationships. The rendered ERD is available at [`docs/erd_diagram.png`](docs/erd_diagram.png), and the Mermaid source is in [`docs/erd.md`](docs/erd.md).

The model follows a clear analytical pattern:

- **Dimensions:** `seasons`, `circuits`, `drivers`, `constructors`, and `status`.
- **Race/event spine:** `races` links a season to a circuit and becomes the common parent for race-level facts.
- **Performance facts:** `results`, `qualifying`, `sprint_results`, `lap_times`, and `pit_stops`.
- **Cumulative snapshots:** `driver_standings` and `constructor_standings`.
- **Constructor race facts:** `constructor_results`.

`results.positionOrder` is used to identify the race winner because `results.position` can be `NULL` for an unclassified finish. In this project, `positionOrder = 1` means winner and `position IS NULL` means a DNF/unclassified result for the relevant analyses.

## SQL learning path

The numbered scripts are intentionally ordered. A reviewer can read them as a curriculum as well as an application.

| File | Role | Key techniques |
|---|---|---|
| `01_schema.sql` | Rebuild the database and create all tables | DDL, primary keys, foreign keys, InnoDB, utf8mb4 |
| `02_load_data.sql` | Import the CSV snapshot | `LOAD DATA LOCAL INFILE`, quoted CSV fields, `\N` NULLs |
| `03_data_profiling.sql` | Establish data quality before analysis | row counts, NULL checks, orphan checks, range checks |
| `04_tier1_foundation.sql` | Basic descriptive queries | `SELECT`, `WHERE`, `GROUP BY`, `HAVING`, aggregates, `COUNT(DISTINCT)` |
| `05_tier2_intermediate.sql` | Relational analysis | joins, anti-joins, subqueries, `CASE`, `UNION ALL`, `COALESCE`, date arithmetic |
| `06_tier3_advanced.sql` | Sequence and ranking analysis | CTEs, window functions, `LAG`, rolling windows, `RANK`, `NTILE`, gaps-and-islands |
| `07_tier4_expert.sql` | Higher-order and performance analysis | recursive CTE, team graph, championship margins, streaks, `CREATE INDEX`, `EXPLAIN ANALYZE` |
| `08_views.sql` | Reusable semantic layer | `v_driver_career_summary` |
| `09_procedures.sql` | Parameterized reporting | `sp_season_report(p_year)` |
| `10_capstone_thesis.sql` | Answer the central question | champion vs runner-up, age, constructors, correlation, reliability |
| `run_all.sql` | One-command orchestration | runs the scripts in dependency order |

The detailed concept-to-query map is in [`docs/sql_concepts_covered.md`](docs/sql_concepts_covered.md).

## The capstone thesis

### 1. Consistency versus peak

`C1` identifies the champion and runner-up from the final driver standings in each season, then joins those drivers back to their season results. It compares wins, DNFs, and win rate. This is a within-season comparison rather than a career comparison, which keeps the question aligned with what it means to win a championship.

### 2. Age curve

`C2` calculates driver age at the date of each race and averages points per race at each age. The `HAVING races >= 100` threshold prevents very small age samples from dominating the chart. The result should be read as a descriptive age profile, not proof of a biological performance law.

### 3. Car versus driver

`C3` finds all distinct championship-winning drivers, then counts the number of constructors for which each driver won a race. This operationalizes “proven across machinery” without pretending that constructor quality is irrelevant.

### 4. Qualifying and points

`C4` creates one row per driver-season, calculates average starting grid and total points, and computes Pearson correlation directly in MySQL because MySQL does not provide a built-in `CORR()` aggregate. Zero-point driver-seasons are retained when a driver has a valid average grid; excluding them would change the population and make the notebook disagree with the capstone SQL.

The correlation is associative, not causal. It also mixes eras with different scoring systems, so it is best interpreted as a directional relationship within this dataset.

### 5. Reliability

`C5` classifies a curated set of status labels as mechanical failures and compares the resulting rate for champions with the rest of the field. The list is intentionally visible in the SQL so a reviewer can challenge or extend it. It excludes driver-error labels such as accidents and collisions and is therefore a mechanical-DNF measure, not an all-cause DNF measure.

## Recorded findings and interpretation

The saved output files report the following supporting evidence:

- Lewis Hamilton is the winningest driver in the current snapshot with 105 wins, followed by Michael Schumacher with 91 and Max Verstappen with 63.
- Mercedes won eight consecutive constructors' titles from 2014 through 2021; Ferrari's longest recorded run in this output is six from 1999 through 2004.
- Nine championships were decided by one point or less, including Niki Lauda's 0.5-point margin in 1984.
- Under the project's deliberately simple 25-points-per-remaining-race heuristic, the 2021 championship was not decided until the final round.

The strongest interpretation is not that one factor “causes” a title. Rather, the evidence suggests a system-level advantage: high-performing drivers in reliable machinery accumulate wins and avoid losing too many races to mechanical failure. Qualifying helps create opportunity, but it does not fully explain the final championship outcome.

## Setup and reproducibility

### Requirements

- MySQL 8.0.18 or newer; `EXPLAIN ANALYZE` in the performance section requires a sufficiently recent 8.0 release.
- MySQL client with `LOCAL INFILE` enabled.
- Python 3.9+ for the notebook. Python packages are listed in [`requirements.txt`](requirements.txt).

### Build the database

Open a terminal in this directory—the `sql/` directory is important because the loader uses portable relative paths:

```powershell
cd "<clone-path>\imarticus projects\sql"
mysql --local-infile=1 -u root -p
```

Inside the MySQL client, run:

```sql
SET GLOBAL local_infile = 1;
SOURCE run_all.sql;
```

If the `SET GLOBAL` statement requires administrative privileges, enable `local_infile=1` in the MySQL server configuration and restart MySQL. `LOCAL INFILE` is used deliberately so the client reads the CSVs; the files do not need to be copied into MySQL's server-side import directory.

To run stages individually:

```powershell
mysql --local-infile=1 -u root -p < 01_schema.sql
mysql --local-infile=1 -u root -p < 02_load_data.sql
mysql -u root -p f1_analytics < 03_data_profiling.sql
```

The full runner rebuilds `f1_analytics`, loads the included CSV snapshot, runs the profiling checks, creates the reusable view and stored procedure, and executes the capstone queries. Re-running `01_schema.sql` is destructive because it drops and recreates the database.

### Run the notebook

Create a virtual environment and install the notebook dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Set database connection variables rather than editing credentials into the notebook:

```powershell
$env:F1_DB_HOST = "127.0.0.1"
$env:F1_DB_PORT = "3306"
$env:F1_DB_NAME = "f1_analytics"
$env:F1_DB_USER = "root"
$env:F1_DB_PASSWORD = "your-password"
jupyter notebook notebook/f1_analysis.ipynb
```

The notebook discovers the project root relative to its own location, queries the live MySQL database, displays the result tables, and saves the six charts under `results/screenshots/`. It intentionally does not contain a password in source code.

## Output folders

- `results/*.txt` contains captured MySQL output from a recorded run.
- `results/screenshots/` contains the six presentation charts generated by the notebook.
- `docs/findings_report.md` gives a compact written interpretation of the thesis results.
- `notebook/f1_analysis.ipynb` is the interactive reviewer walkthrough.

The `.txt` files are useful for a quick review on GitHub, but they are evidence of the recorded run—not a substitute for re-running the SQL against the database.

## Limitations and responsible interpretation

- Points systems changed substantially across F1 history, so absolute career points are not comparable across eras. The analysis favors rates, within-season comparisons, and ranks where possible.
- “Champion” means the driver in position 1 of the final available standings snapshot for a season.
- “Runner-up” means position 2 in that same final snapshot.
- “Mechanical DNF” is a curated status-code classification. It is useful for a transparent case study but not an official FIA reliability taxonomy.
- The qualifying analysis uses `results.grid`, the actual race-start grid, because it is available across the full time span. The dedicated `qualifying` table begins later in the dataset.
- The championship-decider query is explicitly a heuristic. It assumes a 25-point maximum per remaining race and does not fully model historical scoring systems, sprint weekends, fastest-lap points, or tie-break rules.
- Observational SQL summaries do not establish causality. Constructor resources, regulations, teammate quality, strategy, injuries, and changing field sizes are not fully controlled for.

## Suggested reviewer path

1. Read this README for the question, definitions, and limitations.
2. Open [`docs/erd.md`](docs/erd.md) to understand the relational model.
3. Run `01_schema.sql`, `02_load_data.sql`, and `03_data_profiling.sql`.
4. Read the tier files in order, using [`docs/sql_concepts_covered.md`](docs/sql_concepts_covered.md) as a map.
5. Inspect [`10_capstone_thesis.sql`](10_capstone_thesis.sql) and compare it with [`docs/findings_report.md`](docs/findings_report.md).
6. Open and run [`notebook/f1_analysis.ipynb`](notebook/f1_analysis.ipynb) to see the evidence presented as charts.

This sequence lets a reviewer evaluate the project at three levels: database correctness, SQL technique, and analytical communication.
