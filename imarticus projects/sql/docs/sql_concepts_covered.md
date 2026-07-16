# SQL Concepts Coverage

Every concept in the brief, mapped to the query that demonstrates it. All run on MySQL 8.0.

| Concept | File | Query |
|---|---|---|
| `SELECT` / `WHERE` / `ORDER BY` | 04_tier1_foundation.sql | Q1 |
| `GROUP BY` on derived column | 04_tier1_foundation.sql | Q2 |
| Aggregates + `LIMIT` | 04_tier1_foundation.sql | Q3 |
| `GROUP BY` / `HAVING` | 04_tier1_foundation.sql | Q4 |
| Aggregate vs aggregate (subquery) | 04_tier1_foundation.sql | Q5 |
| `WHERE` on `DATE` | 04_tier1_foundation.sql | Q6 |
| `COUNT(DISTINCT)` | 04_tier1_foundation.sql | Q7 |
| Multi-table `INNER JOIN` | 05_tier2_intermediate.sql | Q1 |
| Anti-join subquery (`NOT IN`) | 05_tier2_intermediate.sql | Q2 |
| Ratio + `NULLIF` | 05_tier2_intermediate.sql | Q3 |
| `CASE` + date arithmetic (`TIMESTAMPDIFF`) | 05_tier2_intermediate.sql | Q4 |
| Self-comparison join | 05_tier2_intermediate.sql | Q5 |
| Self-`JOIN` | 05_tier2_intermediate.sql | Q6 |
| `UNION ALL` | 05_tier2_intermediate.sql | Q7 |
| `ORDER BY` + `SEC_TO_TIME` | 05_tier2_intermediate.sql | Q8 |
| `LEFT JOIN` + `COALESCE` | 05_tier2_intermediate.sql | Q9 |
| `COUNT(DISTINCT)` + `HAVING` | 05_tier2_intermediate.sql | Q10 |
| `RANK` vs `DENSE_RANK` | 06_tier3_advanced.sql | Q1 |
| `SUM() OVER` running total | 06_tier3_advanced.sql | Q2 |
| `ROW_NUMBER` partitioned | 06_tier3_advanced.sql | Q3 |
| `LAG` | 06_tier3_advanced.sql | Q4 |
| Window `AVG` over group | 06_tier3_advanced.sql | Q5 |
| `ROWS BETWEEN` frame (rolling avg) | 06_tier3_advanced.sql | Q6 |
| Gaps-and-islands (streaks) | 06_tier3_advanced.sql | Q7 |
| `MIN() OVER ()` | 06_tier3_advanced.sql | Q8 |
| `NTILE` | 06_tier3_advanced.sql | Q9 |
| `ROW_NUMBER` career ordering | 06_tier3_advanced.sql | Q10 |
| CTE + `LAG` change detection | 07_tier4_expert.sql | Q1 |
| **Recursive CTE** (`WITH RECURSIVE`) | 07_tier4_expert.sql | Q2 |
| Conditional aggregation (`CASE`+`MAX`) | 07_tier4_expert.sql | Q3 |
| Multi-CTE analytical heuristic | 07_tier4_expert.sql | Q4 |
| Gaps-and-islands on titles | 07_tier4_expert.sql | Q5 |
| Head-to-head career alignment | 07_tier4_expert.sql | Q7 |
| `CREATE INDEX` + `EXPLAIN ANALYZE` | 07_tier4_expert.sql | Q8 |
| `CREATE VIEW` | 08_views.sql | v_driver_career_summary |
| Stored procedure (`CREATE PROCEDURE`, params) | 09_procedures.sql | sp_season_report |
| Pearson correlation by hand (no `corr()`) | 10_capstone_thesis.sql | C4 |
| Curated `IN`-set classification | 10_capstone_thesis.sql | C5 |

**MySQL-specific notes**
- No `corr()` aggregate → Pearson computed from sums in C4.
- No `FULL OUTER JOIN` → not required; `LEFT`/`RIGHT` cover the brief.
- `CREATE INDEX` has no `IF NOT EXISTS` in 8.0 → Tier 4 Q8 drops-if-exists first so it re-runs cleanly.
- `rows`, `rank`, `lead` are reserved words → back-ticked or aliased where used.
