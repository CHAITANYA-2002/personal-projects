# Findings — What Makes a Champion?

*F1 World Championship data, 1950–2024. All figures below are produced by the queries in
`10_capstone_thesis.sql` (and Tier 3–4 files), run live against the `f1_analytics` MySQL database.
Champion = P1 in the drivers' standings at a season's final round. DNF = classified finishing
position is `NULL`.*

---

## 1. Consistency vs Peak — champions win more **and** break down less

| Finishing | Avg wins / season | Avg DNFs / season | Avg win rate |
|-----------|------------------:|------------------:|-------------:|
| Champion  | **6.37** | **2.24** | **43.3%** |
| Runner-up | 3.45 | 3.07 | 23.3% |

Champions out-win their nearest rival almost 2-to-1 *and* retire less often. It is not peak-or-
consistency — the title-winning season is both. (`10_capstone_thesis.sql` C1)

## 2. The age factor — a broad plateau, no rookie curse

Average points per race is roughly flat across a driver's career, sitting between ~1.5 and ~2.5
from the early 20s into the mid-30s (ages with 100+ starts). The chart does not show a simple rookie
penalty or a single universal peak; competitiveness appears sustained rather than spiky, although
age is confounded with era, car quality, and who receives a race seat. (C2, chart `03_age_curve.png`)

## 3. Car vs driver — the great ones prove it across machinery

**26 of 34** race-winning champions (**76.5%**) won grands prix for **more than one constructor**.
The car clearly matters, but the champion-calibre driver wins in more than one of them.
(C3, chart `04_multi_team_champions.png`)

## 4. Grid-to-race conversion — qualifying matters, moderately

Pearson correlation between a driver-season's **average grid position** and its **total points**:
**r = −0.4222** across 3,040 driver-seasons (negative because a lower grid number = a better slot).
Zero-point driver-seasons with a valid average grid are retained. This is a real, moderate effect —
starting near the front helps — but far from deterministic.
MySQL has no `corr()`, so this is computed by hand from Σx, Σy, Σxy, Σx², Σy² in SQL. (C4)

## 5. Reliability — the decisive edge

| Group | Mechanical-DNF rate |
|-------|--------------------:|
| Champions | **9.58%** |
| Rest of field | 23.51% |

Champions retire from **mechanical failure at less than half the field's rate** — the single starkest
gap in the whole study. Winning a title is as much about the car finishing as the driver starring.
(C5, chart `06_reliability.png`)

---

## Supporting evidence (Tiers 3–4)

- **Winningest drivers:** Hamilton 105, Schumacher 91, Verstappen 63 (view `v_driver_career_summary`).
- **Constructor dynasties:** Mercedes took **8 straight** constructors' titles (2014–2021); Ferrari 6
  (1999–2004). (Tier 4 Q5)
- **Razor-thin titles:** nine championships were decided by ≤1 point — Lauda by **0.5** in 1984;
  Hamilton, Räikkönen, Schumacher, Hunt and others by exactly 1. (Tier 4 Q6)
- **Latest-ever decider:** 2021 went to the final round (22 of 22) — the Abu Dhabi finale. (Tier 4 Q4)

## The one-line answer
> A champion is the driver fast enough to win often, in machinery reliable enough to finish when the
> fast-but-fragile cannot — sustained across cars and across a career, not a single peak season.

## Method notes & caveats
- Points systems changed repeatedly since 1950, so absolute points are **not** comparable across
  eras; era-sensitive questions use per-race rates or within-season margins.
- "Mechanical DNF" uses a curated set of machine-failure status codes (see the C5 query); driver
  errors (Accident, Collision, Spun off) and classified finishes (+N Laps) are excluded.
- Pole/qualifying analysis uses grid position (`grid = 1`), which reflects the actual race start
  across all eras; the dedicated `qualifying` table only begins in 1994.
