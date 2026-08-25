<div align="center">

# 🏎️ Formula 1 — What Makes a Champion?

**A SQL case study across 75 seasons of Formula 1 (1950–2024), delivered as a MySQL database, two analysis notebooks, and an interactive dashboard.**

[![Streamlit](https://img.shields.io/badge/Live%20demo-Streamlit-e10600?style=for-the-badge&logo=streamlit&logoColor=white)](#-live-demo)
[![MySQL](https://img.shields.io/badge/MySQL-8.0-00618A?style=for-the-badge&logo=mysql&logoColor=white)](#-running-it-locally)
[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](requirements.txt)
[![Notebooks](https://img.shields.io/badge/Jupyter-EDA-F37626?style=for-the-badge&logo=jupyter&logoColor=white)](notebook/)

![Overview](docs/app/01-overview.jpg)

</div>

---

## The question

> **What separates a World Champion from the rest of the grid — peak speed, consistency, reliability, the car, or some combination of all four?**

This project does not claim SQL can prove causation. It shows how a well-modelled racing database can be queried to *produce evidence*, how that evidence should be read responsibly, and how every number can be reproduced by a reviewer.

## The answer, in five numbers

| Question | Result | Reading |
|---|---:|---|
| Do champions win more? | **6.37** wins/season vs **3.45** for runners-up | Higher peak output across the title season. |
| Do they finish more often? | **2.24** DNFs/season vs **3.07** | The edge is not only speed — it is races not lost. |
| Does qualifying matter? | Pearson **r = −0.42** over 3,040 driver-seasons | Real but moderate; a good grid slot is not destiny. |
| Is it just the car? | **26 of 34** champions (**76.5%**) won for **>1 constructor** | Champion-calibre drivers win in more than one machine. |
| Is reliability decisive? | Mechanical DNF **9.58%** vs **23.51%** for the field | The single starkest gap in the study. |

> **A champion is the driver fast enough to win often, in machinery reliable enough to finish when the fast-but-fragile cannot.**

Full write-up: [`docs/findings_report.md`](docs/findings_report.md).

---

## 🚀 Live demo

**▶ [Open the live dashboard](https://share.streamlit.io)** *(link added once deployed — see [Deploying](#-deploying-your-own-copy))*

The deployed app runs on a bundled read-only **SQLite** build of the same database, so it needs no server. Locally it connects to **MySQL** automatically when one is reachable.

---

## The dashboard

Six pages, every figure computed live by SQL — nothing is hard-coded.

### Drivers — career records and a driver dossier
Filter by career starts, page through 266 drivers, then drill into any one of them: portrait, team, telemetry and season-by-season points.

![Drivers](docs/app/02-drivers.jpg)

### Race Explorer — any Grand Prix in history
Real circuit geometry, a podium stand with driver portraits and team marks, and a fully colour-coded classification table.

![Race Explorer](docs/app/03-race-explorer.jpg)

### Constructors — team dynasties
Race wins and championships in authentic team colours, plus a register of entries, podiums, poles, points and active eras.

![Constructors](docs/app/04-constructors.jpg)

### The Champion Thesis — the five questions, answered visually
![Champion Thesis](docs/app/06-champion-thesis.jpg)

### SQL Query Runner — the queries and the schema
Run the showcase queries against the live database, then explore an interactive ERD with every primary and foreign key.

![SQL Runner](docs/app/05-sql-runner.jpg)

---

## What is in this repository

```
sql/
├── app.py                    # Streamlit dashboard (MySQL locally, SQLite when deployed)
├── 01_schema.sql             # 14 tables, 23 foreign keys
├── 02_load_data.sql          # LOAD DATA LOCAL INFILE for the CSV snapshot
├── 03_data_profiling.sql     # row counts, NULL audit, orphan checks
├── 04_tier1_foundation.sql   # SELECT · WHERE · GROUP BY · HAVING
├── 05_tier2_intermediate.sql # joins · subqueries · CASE · UNION · dates
├── 06_tier3_advanced.sql     # CTEs · window functions · LAG · NTILE · gaps-and-islands
├── 07_tier4_expert.sql       # recursive CTE · streaks · CREATE INDEX · EXPLAIN ANALYZE
├── 08_views.sql              # v_driver_career_summary
├── 09_procedures.sql         # sp_season_report(year)
├── 10_capstone_thesis.sql    # the five thesis queries
├── run_all.sql               # rebuild + load + profile in one command
├── notebook/
│   ├── 01_data_cleaning_feature_engineering.ipynb
│   └── 02_f1_eda.ipynb
├── data/                     # 14 source CSVs + bundled SQLite build
├── docs/                     # ERD, findings report, concept map, app screenshots
├── results/                  # captured query output + presentation charts
└── assets/                   # circuit geometry, driver portraits, team marks
```

## The SQL learning path

The numbered scripts are deliberately ordered — they read as a curriculum as well as an application.

| File | Role | Techniques |
|---|---|---|
| `01_schema.sql` | Build the database | DDL, PK/FK, InnoDB, utf8mb4 |
| `02_load_data.sql` | Import the snapshot | `LOAD DATA LOCAL INFILE`, quoted CSV, `\N` NULLs |
| `03_data_profiling.sql` | Prove the load is clean | row counts, NULL audit, orphan checks |
| `04_tier1_foundation.sql` | Describe the data | `SELECT`, `GROUP BY`, `HAVING`, `COUNT(DISTINCT)` |
| `05_tier2_intermediate.sql` | Relate the data | joins, anti-joins, correlated subqueries, `CASE`, `UNION ALL`, `COALESCE`/`NULLIF` |
| `06_tier3_advanced.sql` | Sequence and rank | CTEs, `RANK`/`DENSE_RANK`, `LAG`, rolling frames, `NTILE`, gaps-and-islands |
| `07_tier4_expert.sql` | Go further | **recursive CTE** (teammate graph), title streaks, `CREATE INDEX` + `EXPLAIN ANALYZE` |
| `08_views.sql` · `09_procedures.sql` | Reusable layer | `CREATE VIEW`, parameterised `CREATE PROCEDURE` |
| `10_capstone_thesis.sql` | Answer the question | champion vs runner-up, age curve, constructors, correlation, reliability |

Concept-to-query map: [`docs/sql_concepts_covered.md`](docs/sql_concepts_covered.md).

## The relational model

14 tables, 23 foreign keys. Dimensions (`drivers`, `constructors`, `circuits`, `seasons`, `status`) surround the `races` spine; facts (`results`, `qualifying`, `lap_times`, `pit_stops`) and cumulative snapshots (`driver_standings`, `constructor_standings`) hang off it.

![ERD](docs/erd_diagram.png)

Mermaid source: [`docs/erd.md`](docs/erd.md).

`results.positionOrder` identifies the winner because `results.position` is `NULL` for an unclassified finish. Throughout the project **`positionOrder = 1` means winner** and **`position IS NULL` means DNF**.

## The notebooks

| Notebook | What it does |
|---|---|
| [`01_data_cleaning_feature_engineering.ipynb`](notebook/01_data_cleaning_feature_engineering.ipynb) | Profiles all 14 tables, audits missing values (and explains what `NULL` *means* in F1), verifies referential integrity, then engineers three analysis tables: `f1_result_features`, `f1_driver_features`, `f1_season_driver`. |
| [`02_f1_eda.ipynb`](notebook/02_f1_eda.ipynb) | Exploratory analysis over those tables — geography, demographics, constructors, reliability by era, the champion thesis and a correlation overview, in ~15 seaborn charts driven by live SQL. |

Notebook 2 depends on the tables built by Notebook 1, so run them in order.

---

## 💻 Running it locally

### 1. Build the database

```powershell
# from the sql/ directory, with local-infile enabled
mysql --local-infile=1 -u root -p
```
```sql
SET GLOBAL local_infile = 1;
SOURCE run_all.sql;     -- schema → load → profiling
```

> Re-running `01_schema.sql` is destructive: it drops and recreates `f1_analytics`.

### 2. Install the Python dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Point at your database (optional)

The app and notebooks default to `root` / `root` / `127.0.0.1`. Override with environment variables instead of editing code:

```powershell
$env:F1_DB_HOST = "127.0.0.1"
$env:F1_DB_PORT = "3306"
$env:F1_DB_NAME = "f1_analytics"
$env:F1_DB_USER = "root"
$env:F1_DB_PASSWORD = "your-password"
```

### 4. Run the notebooks, then the dashboard

```powershell
jupyter notebook notebook/         # run 01 first, then 02
python -m streamlit run app.py     # dashboard on http://localhost:8501
```

If no MySQL server answers, the dashboard automatically falls back to the bundled `data/f1_analytics.sqlite` build so it still runs.

---

## ☁️ Deploying your own copy

The app is deployment-ready: the bundled SQLite build means **no database server is required**.

1. Push this repository to GitHub.
2. Sign in at **[share.streamlit.io](https://share.streamlit.io)** with that GitHub account.
3. **Create app** → pick the repository and branch.
4. Set **Main file path** to `imarticus projects/sql/app.py`.
5. Deploy. The app detects that no MySQL is reachable and serves the bundled SQLite database.

To point a deployment at a real MySQL server instead, add a `F1_DB_URL` secret in the Streamlit app settings:

```toml
F1_DB_URL = "mysql+pymysql://user:password@host:3306/f1_analytics?charset=utf8mb4"
```

Once deployed, replace the placeholder in the [Live demo](#-live-demo) section with your app URL.

---

## Limitations and responsible interpretation

- Points systems changed repeatedly since 1950, so absolute career points are **not** comparable across eras. The analysis favours rates, within-season comparisons and ranks.
- **Champion** = position 1 in the final available standings snapshot of a season; **runner-up** = position 2 in that same snapshot.
- **Mechanical DNF** is a curated classification of the source `status` codes, not an official FIA taxonomy. The same list is used by the SQL, the notebooks and the dashboard so all three agree.
- Qualifying analysis uses `results.grid` (the actual race start) because it exists for the whole period; the dedicated `qualifying` table begins in 1994.
- The championship-decider query is an explicit heuristic — it assumes 25 points per remaining race and does not model historical scoring, sprints, fastest-lap points or tie-breaks.
- Observational summaries do not establish causation. Team budgets, regulations, teammate quality, strategy and changing field sizes are not controlled for.

## Suggested reviewer path

1. Read this README for the question, definitions and limitations.
2. Open [`docs/erd.md`](docs/erd.md) for the relational model.
3. Run `01_schema.sql`, `02_load_data.sql`, `03_data_profiling.sql`.
4. Read the tier files in order using [`docs/sql_concepts_covered.md`](docs/sql_concepts_covered.md) as a map.
5. Compare [`10_capstone_thesis.sql`](10_capstone_thesis.sql) with [`docs/findings_report.md`](docs/findings_report.md).
6. Run the two notebooks to see the cleaning pipeline and the exploratory analysis.
7. Launch [`app.py`](app.py) — or open the live demo — to explore the same evidence interactively.

This lets a reviewer judge the work at four levels: **database correctness, SQL technique, analytical communication, and delivery.**

---

## Credits, data sources and trademarks

Full attribution is in **[`CREDITS.md`](CREDITS.md)**. In short:

- **Race data** — Formula 1 World Championship (1950–2024) dataset from Kaggle (*rohanrao*), derived from the Ergast API and published for non-commercial use.
- **Circuit layouts** — real track geometry from [`bacinger/f1-circuits`](https://github.com/bacinger/f1-circuits) (MIT).
- **Driver photographs** — English Wikipedia lead images; licences vary per image (mostly Creative Commons or public domain). Where no photograph exists the interface shows a team-coloured monogram — **never a synthetic or stand-in face**.
- **Constructor logos** — team marks are **registered trademarks of their respective owners**, used nominatively to identify the team whose results are displayed in this non-commercial educational project. No endorsement or affiliation is implied. Teams without an available mark fall back to a generated monogram.

*This repository is coursework. It is not affiliated with, endorsed by, or associated with Formula 1, the FIA, or any Formula One team.*
