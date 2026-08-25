<div align="center">

# Data Analytics Portfolio

**Chaitanya Khandelwal** · SQL · Python · MySQL · Streamlit

</div>

---

## 🏎️ Formula 1 — What Makes a Champion?

A SQL case study across **75 seasons of Formula 1 (1950–2024)**: a 14-table MySQL database, a tiered query curriculum from `SELECT` to recursive CTEs, two EDA notebooks, and an interactive dashboard — all built to answer one question.

### **▶ [Live dashboard](https://f1-what-makes-a-champion.streamlit.app/)** · **[Project README](imarticus%20projects/sql/README.md)**

[![Live demo](https://img.shields.io/badge/Live%20demo-f1--what--makes--a--champion-e10600?style=for-the-badge&logo=streamlit&logoColor=white)](https://f1-what-makes-a-champion.streamlit.app/)
[![MySQL](https://img.shields.io/badge/MySQL-8.0-00618A?style=for-the-badge&logo=mysql&logoColor=white)](imarticus%20projects/sql/01_schema.sql)
[![Notebooks](https://img.shields.io/badge/Jupyter-EDA-F37626?style=for-the-badge&logo=jupyter&logoColor=white)](imarticus%20projects/sql/notebook/)

![F1 dashboard](imarticus%20projects/sql/docs/app/01-overview.jpg)

> **What separates a World Champion from the rest of the grid — peak speed, consistency, reliability, or the car?**

| Question | Result |
|---|---:|
| Do champions win more? | **6.37** wins/season vs **3.45** for runners-up |
| Do they finish more often? | **2.24** DNFs/season vs **3.07** |
| Does qualifying matter? | Pearson **r = −0.42** over 3,040 driver-seasons |
| Is it just the car? | **76.5%** of champions won for more than one constructor |
| Is reliability decisive? | Mechanical DNF **9.58%** vs **23.51%** for the field |

**A champion is the driver fast enough to win often, in machinery reliable enough to finish when the fast-but-fragile cannot.**

<table>
<tr>
<td width="50%"><img src="imarticus%20projects/sql/docs/app/03-race-explorer.jpg" alt="Race Explorer"></td>
<td width="50%"><img src="imarticus%20projects/sql/docs/app/04-constructors.jpg" alt="Constructors"></td>
</tr>
<tr>
<td align="center"><b>Race Explorer</b> — real circuit geometry and podium</td>
<td align="center"><b>Constructors</b> — team dynasties in team colours</td>
</tr>
</table>

**What it demonstrates:** relational modelling · data-quality profiling · joins, window functions, recursive CTEs, views, stored procedures, query tuning with `EXPLAIN ANALYZE` · feature engineering · exploratory analysis · dashboard delivery.

📁 [`imarticus projects/sql/`](imarticus%20projects/sql/) — full project, SQL scripts, notebooks and findings report.

---

## 📊 Startup Funding — Excel Dashboard

An interactive Excel dashboard analysing startup funding data with pivot tables, slicers and summary reporting.

📁 [`imarticus projects/excel/`](imarticus%20projects/excel/)

---

## Repository layout

```
personal-projects/
├── requirements.txt              # dashboard runtime dependencies
├── requirements-notebooks.txt    # extra dependencies for the notebooks
├── imarticus projects/
│   ├── sql/                      # F1 SQL case study + Streamlit dashboard
│   └── excel/                    # Startup funding Excel dashboard
└── class_random/                 # coursework exercises
```

## Running the F1 project locally

```powershell
pip install -r requirements.txt -r requirements-notebooks.txt
cd "imarticus projects/sql"
python -m streamlit run app.py
```

The dashboard uses MySQL when a server is reachable and falls back to the bundled SQLite build otherwise, so it runs either way. Full setup instructions are in the [project README](imarticus%20projects/sql/README.md).

---

*Attribution for all third-party data, imagery and trademarks used in the F1 project is in [`CREDITS.md`](imarticus%20projects/sql/CREDITS.md). These are educational projects and are not affiliated with Formula 1, the FIA, or any Formula One team.*
